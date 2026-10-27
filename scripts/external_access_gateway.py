#!/usr/bin/env python3
"""Temporary authenticated, explicitly scoped gateway for a built AXIOM frontend.

This deliberately does not serve Vite, repository files or a general proxy.
The config and password remain local; --config is the only credential input.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import html
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import struct
import time
from typing import Callable
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

import httpx
from fastapi import FastAPI, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
import uvicorn

UPSTREAM = "http://127.0.0.1:8000"
UPSTREAM_ORIGINS = frozenset(f"http://127.0.0.1:{port}" for port in (8000, 8020, 8021, 8033, 8063, 8072, 8075))
CONTEXT_PATH = "/api/external-viewer-context"
ACCESS_MARKER = b'<meta name="axiom-access-mode" content="external-viewer">'
ENGINEER_MARKER = b'<meta name="axiom-access-mode" content="external-engineer">'
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_JSON_BODY = 8 * 1024 * 1024
COOKIE = "__Host-axiom_view_session"
BLOCKED_PROBES = (
    re.compile(r"^/api/v1/adapters/[^/]+/(?:health|capabilities|probe)/?$"),
    re.compile(r"^/api/v1/jobs/[^/]+/remote-execution/?$"),
    re.compile(r"^/api/v1/projects/[^/]+/tokens(?:/.*)?$"),
    re.compile(r"^/api/v1/object-sim/presets/[^/]+/bind/?$"),
)
READONLY_REMOTE = re.compile(r"^/api/v1/camera-bridge/(?:scene-catalog|sources)/?$")
PROJECT_ADMIN_WRITES = re.compile(r"^/api/v1/projects(?:/[^/]+)?/?$")
STATIC_TYPES = {".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".glb"}
STATIC_NOTICES = {
    "assets/kenney-car-kit/License.txt": "text/plain",
    "assets/kenney-car-kit/manifest.json": "application/json",
}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
BANNER = ('<aside role="note" style="position:fixed;z-index:2147483647;bottom:12px;left:50%;'
          'transform:translateX(-50%);padding:8px 16px;border:1px solid #53737c;border-radius:12px;'
          'background:#102630;color:#e5f8ff;font:12px system-ui;box-shadow:0 4px 20px #0008;'
          'pointer-events:none;max-width:calc(100% - 24px);text-align:center">'
          '외부 조회 전용 · 수정/실행 차단</aside>')


@dataclass(frozen=True)
class GatewayConfig:
    password: str = field(repr=False)
    expires_at: float
    dist: Path
    read_paths: tuple[str, ...]
    upstream: str = UPSTREAM
    upstream_token_file: Path | None = None
    access_mode: str = "read_only"
    write_paths: tuple[dict[str, str], ...] = ()
    expected_project_id: str | None = None


def compile_read_path(path: str) -> re.Pattern:
    """Pin audited OpenAPI templates; parameters match one plain segment only."""
    if not isinstance(path, str) or not path.startswith("/api/v1/") or any(c in path for c in "?#%\\\x00"):
        raise ValueError("Invalid approved read path")
    pieces = path.split("/")
    patterns = []
    for piece in pieces:
        if piece.startswith("{") and piece.endswith("}") and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", piece[1:-1]):
            patterns.append(r"[^/.][^/]*")
        elif "{" in piece or "}" in piece or piece in {".", ".."}:
            raise ValueError("Invalid approved read path")
        else:
            patterns.append(re.escape(piece))
    return re.compile("^" + "/".join(patterns) + "$")


def validate_access_mode(mode: str, write_paths) -> tuple[tuple[str, re.Pattern], ...]:
    if not isinstance(mode, str) or mode not in {"read_only", "project_engineer"}:
        raise ValueError("Unknown external access mode")
    if not isinstance(write_paths, (tuple, list)) or len(write_paths) > 128:
        raise ValueError("Invalid approved write paths")
    if mode == "read_only" and write_paths:
        raise ValueError("Read-only access cannot configure write paths")
    if mode == "project_engineer" and not write_paths:
        raise ValueError("Engineer access requires explicitly approved write paths")
    result = []
    seen = set()
    for item in write_paths:
        if (not isinstance(item, dict) or set(item) != {"method", "path"}
                or not isinstance(item.get("method"), str) or item["method"] not in WRITE_METHODS):
            raise ValueError("Invalid approved write method/path")
        path = item["path"]
        rule = compile_read_path(path)
        key = (item["method"], path)
        if key in seen:
            raise ValueError("Duplicate approved write path")
        seen.add(key)
        result.append((item["method"], rule))
    return tuple(result)


def validate_project_pin(project_id: str | None, *, authenticated: bool):
    if project_id is None:
        if authenticated:
            raise ValueError("Authenticated upstream requires expected_project_id")
    elif not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", project_id):
        raise ValueError("Invalid expected_project_id")


def read_private_file(path: Path, max_bytes: int) -> bytes:
    """Read an owner-only file without following symlink path components."""
    if path.resolve() != path.absolute():
        raise ValueError("Private gateway file path must not contain symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError("Private gateway file must be owned and regular with mode 0600")
        if info.st_size > max_bytes:
            raise ValueError("Private gateway file exceeds its size limit")
        with os.fdopen(fd, "rb") as source:
            fd = -1
            value = source.read(max_bytes + 1)
        if len(value) > max_bytes:
            raise ValueError("Private gateway file exceeds its size limit")
        return value
    finally:
        if fd != -1:
            os.close(fd)

def read_upstream_token(path: Path) -> str:
    if not path.is_absolute():
        raise ValueError("Upstream token file requires an absolute path")
    try:
        token = read_private_file(path, 4096).decode("ascii").strip()
    except UnicodeDecodeError:
        raise ValueError("Invalid upstream token format") from None
    if not re.fullmatch(r"[A-Za-z0-9._~-]{24,512}", token):
        raise ValueError("Invalid upstream token format")
    return token


def load_config(path: Path) -> GatewayConfig:
    value = json.loads(read_private_file(path, 512 * 1024))
    if not isinstance(value, dict) or set(value) - {"password", "expires_at", "dist", "upstream", "read_paths", "upstream_token_file", "access_mode", "write_paths", "expected_project_id"}:
        raise ValueError("Invalid gateway config fields")
    password = value.get("password")
    expires_at = value.get("expires_at")
    dist_value = value.get("dist")
    read_paths = value.get("read_paths")
    if not isinstance(password, str) or not 24 <= len(password.encode("utf-8")) <= 256:
        raise ValueError("Gateway requires a generated password of 24 to 256 bytes")
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or not math.isfinite(expires_at):
        raise ValueError("Invalid gateway expiration")
    now = time.time()
    if not now < expires_at <= now + 48 * 60 * 60 + 5:
        raise ValueError("Gateway expiration must be within the next 48 hours")
    if not isinstance(dist_value, str) or not Path(dist_value).is_absolute():
        raise ValueError("Gateway requires an absolute production dist path")
    if not isinstance(read_paths, list) or not read_paths or len(read_paths) > 512:
        raise ValueError("Gateway requires an audited read_paths list")
    for read_path in read_paths:
        compile_read_path(read_path)
    mode = value.get("access_mode", "read_only")
    write_paths = value.get("write_paths", [])
    validate_access_mode(mode, write_paths)
    upstream = value.get("upstream", UPSTREAM)
    if not isinstance(upstream, str) or upstream not in UPSTREAM_ORIGINS:
        raise ValueError("Only explicitly allowed loopback API origins are supported")
    token_path = value.get("upstream_token_file")
    if token_path is not None and (not isinstance(token_path, str) or not Path(token_path).is_absolute()):
        raise ValueError("Upstream token file requires an absolute path")
    if mode == "project_engineer" and token_path is None:
        raise ValueError("Engineer access requires a private Engineer token file")
    if upstream != UPSTREAM and token_path is None:
        raise ValueError("This upstream requires a private Viewer token file")
    expected_project_id = value.get("expected_project_id")
    validate_project_pin(expected_project_id, authenticated=token_path is not None)
    if token_path is not None:
        read_upstream_token(Path(token_path))
    return GatewayConfig(password, float(expires_at), Path(dist_value), tuple(read_paths), upstream,
                         Path(token_path) if token_path is not None else None, mode, tuple(write_paths), expected_project_id)


def load_static(dist: Path, *, engineer: bool = False) -> dict[str, tuple[bytes, str]]:
    """Snapshot only production index and recognized assets, with no symlinks."""
    if not dist.is_dir() or dist.is_symlink() or dist.resolve() != dist.absolute():
        raise ValueError("Production dist must be a real directory without symlink components")
    index = dist / "index.html"
    if not index.is_file() or index.is_symlink():
        raise ValueError("Production dist is missing a regular index.html")
    index_bytes = index.read_bytes()
    if len(index_bytes) > 2 * 1024 * 1024 or b"</body>" not in index_bytes:
        raise ValueError("Invalid production index")
    marker = ENGINEER_MARKER if engineer else ACCESS_MARKER
    if b"</head>" in index_bytes:
        index_bytes = index_bytes.replace(b"</head>", marker + b"</head>", 1)
    elif b"<body" in index_bytes:
        index_bytes = index_bytes.replace(b"<body", b"<head>" + marker + b"</head><body", 1)
    else:
        raise ValueError("Production index is missing a marker insertion point")
    banner = BANNER.replace("외부 조회 전용 · 수정/실행 차단", "외부 프로젝트 작업 · 수정·실행 가능") if engineer else BANNER
    index_bytes = index_bytes.replace(b"</body>", banner.encode() + b"</body>", 1)
    files = {"/": (index_bytes, "text/html"), "/index.html": (index_bytes, "text/html")}
    total = len(index_bytes)
    assets = dist / "assets"
    if assets.exists():
        if not assets.is_dir() or assets.is_symlink():
            raise ValueError("Production assets must be a real directory")
        for item in assets.rglob("*"):
            if item.is_symlink():
                raise ValueError("Symlinks are not allowed in production assets")
            relative = item.relative_to(dist)
            notice_type = STATIC_NOTICES.get(relative.as_posix())
            if (not item.is_file() or (item.suffix.lower() not in STATIC_TYPES and notice_type is None)
                    or any(p.startswith(".") for p in relative.parts)):
                continue
            if item.resolve() != item.absolute() or not item.resolve().is_relative_to(dist.resolve()):
                raise ValueError("Asset escapes production dist")
            size = item.stat().st_size
            total += size
            if size > 32 * 1024 * 1024 or total > 100 * 1024 * 1024:
                raise ValueError("Production static snapshot exceeds gateway bounds")
            data = item.read_bytes()
            media_type = notice_type or mimetypes.guess_type(item.name)[0] or "application/octet-stream"
            if item.suffix.lower() == '.glb':
                if len(data) < 12 or struct.unpack_from('<4sII', data) != (b'glTF', 2, len(data)):
                    raise ValueError('Invalid production GLB2 asset')
                media_type = 'model/gltf-binary'
            files["/" + relative.as_posix()] = (data, media_type)
    # Preserve distributed OSS notices without exposing arbitrary text files.
    licenses = dist / "licenses"
    if licenses.exists() or licenses.is_symlink():
        if not licenses.is_dir() or licenses.is_symlink():
            raise ValueError("Production licenses must be a real directory")
        for item in licenses.iterdir():
            if item.is_symlink():
                raise ValueError("Symlinks are not allowed in production licenses")
            if not item.is_file() or item.suffix != ".txt" or item.name.startswith("."):
                continue
            if item.resolve() != item.absolute() or not item.resolve().is_relative_to(dist.resolve()):
                raise ValueError("License escapes production dist")
            size = item.stat().st_size
            total += size
            if size > 32 * 1024 * 1024 or total > 100 * 1024 * 1024:
                raise ValueError("Production static snapshot exceeds gateway bounds")
            files["/licenses/" + item.name] = (item.read_bytes(), "text/plain")
    favicon = dist / "favicon.svg"
    if favicon.exists():
        if favicon.is_symlink() or not favicon.is_file() or favicon.stat().st_size > 1024 * 1024:
            raise ValueError("Invalid production favicon")
        files["/favicon.svg"] = (favicon.read_bytes(), "image/svg+xml")
    return files


def login_landing(project_id: str | None, return_hash: str = "") -> str:
    """Keep a bounded SPA bookmark on this origin and always pin its project."""
    if project_id is None:
        return "/"
    route, query = "workspace", []
    if return_hash.startswith("#") and len(return_hash) <= 2048:
        candidate, _, params = return_hash[1:].partition("?")
        if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", candidate):
            try:
                query = [(key, value) for key, value in parse_qsl(params, keep_blank_values=True, max_num_fields=64)
                         if key != "project"]
                route = candidate
            except ValueError:
                query = []
    return "/#" + route + "?" + urlencode([*query, ("project", project_id)])


def login_page(message: str = "", *, engineer: bool = False, preserve_route: bool = False,
               return_hash: str = "") -> str:
    page = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AXIOM · 외부 조회</title>
<style>html{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(ellipse at top,#183c45,#091219 70%);font:15px/1.65 system-ui,sans-serif;color:#e4edf3}.card{width:min(440px,calc(100% - 32px));padding:36px;border:1px solid #304853;border-radius:22px;background:#0f1c25;box-shadow:0 28px 100px #0006}.brand{font-size:13px;letter-spacing:.2em;color:#71d2cf}h1{font-size:27px;line-height:1.3;margin:12px 0}p{color:#a6b9c5}.tag{display:inline-block;padding:4px 10px;border:1px solid #35585c;border-radius:8px;color:#8bdbd6;font-size:12px}label{display:block;margin:24px 0 7px}input,button{width:100%;padding:12px 14px;border-radius:10px;font:inherit}input{background:#09141c;border:1px solid #415864;color:#fff}button{margin-top:14px;background:#90ded4;color:#082822;border:0;font-weight:700;cursor:pointer}.error{color:#ffb6b6;min-height:24px;font-size:13px}footer{margin-top:24px;color:#7e96a6;font-size:12px}</style></head><body><main class="card"><div class="brand">AXIOM / EXTERNAL VIEW</div><h1>시험 결과를 살펴보세요</h1><span class="tag">외부 조회 전용 · 수정/실행 차단</span><p>공유받은 비밀번호로 기존 화면과 결과를 열 수 있습니다.</p><form action="/login" method="post"><label for="password">접속 비밀번호</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="256" autofocus><button type="submit">결과 화면 열기</button></form><div class="error" role="status">''' + html.escape(message) + '''</div><footer>임시 접속 주소는 정해진 시간에 만료됩니다.<br>연결 중에는 실행 중인 Mac이 켜져 있고 잠자기 상태가 아니어야 합니다.</footer></main></body></html>'''
    if engineer:
        page = (page.replace("AXIOM · 외부 조회", "AXIOM · 외부 프로젝트 작업")
                .replace("EXTERNAL VIEW", "EXTERNAL WORKSPACE")
                .replace("시험 결과를 살펴보세요", "프로젝트 작업을 이어가세요")
                .replace("외부 조회 전용 · 수정/실행 차단", "외부 프로젝트 작업 · 수정·실행 가능")
                .replace("공유받은 비밀번호로 기존 화면과 결과를 열 수 있습니다.", "공유받은 프로젝트에서 허용된 수정과 시험 실행을 진행할 수 있습니다.")
                .replace("결과 화면 열기", "프로젝트 열기"))
    if preserve_route:
        # URL fragments never reach HTTP. Capture only the browser's SPA hash;
        # login_landing rejects external targets and supplies the trusted pin.
        value = return_hash if len(return_hash) <= 2048 else ""
        page = page.replace('</form>', '<input type="hidden" id="return_hash" name="return_hash" value="'
                            + html.escape(value, quote=True) + '"></form>', 1)
        page = page.replace('</body>', '<script>if(location.hash&&location.hash.length<=2048)'
                            '{document.getElementById("return_hash").value=location.hash;}</script></body>', 1)
    return page



def same_origin(request: Request) -> bool:
    if request.headers.get("sec-fetch-site", "") not in {"", "same-origin", "none"}:
        return False
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        parsed = urlsplit(origin)
        current = urlsplit(str(request.url))
        return (parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password
                and parsed.path == "" and not parsed.query and not parsed.fragment
                and (parsed.scheme, parsed.netloc.lower()) == (current.scheme, current.netloc.lower()))
    except ValueError:
        return False


def safe_path(request: Request) -> bool:
    raw = request.scope.get("raw_path", b"").lower()
    path = request.url.path
    # Disallow encoded separators/dot segments, repeated decoding and controls.
    return (not any(piece in raw for piece in (b"%", b"\\", b"\x00"))
            and not any(ord(c) < 32 or ord(c) == 127 for c in path)
            and "\\" not in path and "//" not in path
            and all(piece not in {".", ".."} for piece in path.split("/")))


def create_app(config: GatewayConfig, *, transport: httpx.AsyncBaseTransport | None = None,
               clock: Callable[[], float] = time.time) -> FastAPI:
    approved_writes = validate_access_mode(config.access_mode, config.write_paths)
    engineer = config.access_mode == "project_engineer"
    expected_role = "Engineer" if engineer else "Viewer"
    if config.upstream not in UPSTREAM_ORIGINS:
        raise ValueError("Only explicitly allowed loopback API origins are supported")
    if engineer and config.upstream_token_file is None:
        raise ValueError("Engineer access requires a private Engineer token file")
    if config.upstream != UPSTREAM and config.upstream_token_file is None:
        raise ValueError("This upstream requires a private Viewer token file")
    validate_project_pin(config.expected_project_id, authenticated=config.upstream_token_file is not None)
    upstream_token = read_upstream_token(config.upstream_token_file) if config.upstream_token_file else None
    assets = load_static(config.dist, engineer=engineer)
    approved = tuple(compile_read_path(path) for path in config.read_paths)
    sessions: dict[str, float] = {}
    attempts: deque[float] = deque(maxlen=8)
    slots = asyncio.Semaphore(8)

    async def verify_role(client: httpx.AsyncClient):
        # One fixed read-only identity request, before accepting any connections.
        # A valid bearer alone does not establish the approved role: reject
        # operators and other roles even when the credential file is well formed.
        try:
            async with client.stream("GET", config.upstream + "/api/v1/team/whoami",
                                     headers={"Authorization": "Bearer " + upstream_token,
                                              "Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    raise ValueError("Rejected identity")
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 16 * 1024:
                        raise ValueError("Identity response exceeds its bound")
                identity = json.loads(payload)
                permissions = identity.get("permissions", {})
                if (identity.get("mode") != "project" or identity.get("role") != expected_role
                        or identity.get("project_id") != config.expected_project_id
                        or identity.get("legacy_domains_available") is not False
                        or permissions.get("read") is not True
                        or permissions.get("execute") is not engineer
                        or any(permissions.get(key) is not False for key in ("review", "admin"))):
                    raise ValueError("Insufficient identity boundary")
            client.cookies.clear()
        except Exception:
            raise RuntimeError(f"Upstream {expected_role} role verification failed") from None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False,
                                    timeout=httpx.Timeout(30, connect=3, write=5, pool=3),
                                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4)) as client:
            if upstream_token:
                try:
                    await asyncio.wait_for(verify_role(client), timeout=5)
                except TimeoutError:
                    raise RuntimeError(f"Upstream {expected_role} role verification failed") from None
            app.state.upstream_client = client
            yield
        sessions.clear()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def reload_static_snapshot() -> bool:
        """Owner-local reload of this fixed dist; never reload config or credentials.

        Build and validate the complete replacement before the single dictionary
        swap. Requests already reading the old snapshot can finish normally;
        invalid builds keep the previous UI and its authenticated sessions.
        This callable has no HTTP route. Only the process owner signals main().
        """
        nonlocal assets
        if clock() >= config.expires_at:
            app.state.static_reload_status = "expired"
            return False
        try:
            replacement = load_static(config.dist, engineer=engineer)
        except (OSError, ValueError):
            # Paths and error contents may be private: retain a fixed status.
            app.state.static_reload_status = "rejected"
            return False
        assets = replacement
        app.state.static_reload_generation += 1
        app.state.static_reload_status = "ready"
        return True

    app.state.reload_static_snapshot = reload_static_snapshot
    app.state.static_reload_generation = 0
    app.state.static_reload_status = "ready"

    @app.middleware("http")
    async def barrier(request: Request, call_next):
        if clock() >= config.expires_at:
            response = JSONResponse({"detail": "임시 외부 접속이 만료되었습니다."}, status_code=410)
        elif not safe_path(request):
            response = JSONResponse({"detail": "허용되지 않은 경로입니다."}, status_code=400)
        elif request.url.path == "/login" and request.method in {"GET", "POST", "HEAD"}:
            response = await call_next(request)
        else:
            token = request.cookies.get(COOKIE, "")
            expiry = sessions.get(token, 0)
            if expiry <= clock():
                sessions.pop(token, None)
                response = (JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
                            if request.url.path.startswith("/api/") else RedirectResponse("/login", status_code=303))
            elif (request.method not in {"GET", "HEAD"}
                  and not (request.url.path == "/logout" and request.method == "POST")
                  and not (engineer and request.url.path.startswith("/api/v1/") and request.method in WRITE_METHODS)):
                response = JSONResponse({"detail": "외부 조회 전용입니다. 수정과 시험 실행은 차단되어 있습니다."}, status_code=403)
            else:
                response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path == "/login" or (engineer and request.url.path in {"/", "/index.html"}):
            # Preserve same-origin form Origin in browsers that make it opaque
            # under no-referrer. Result and API pages retain no-referrer.
            response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.api_route("/login", methods=["GET", "HEAD", "POST"])
    async def login(request: Request):
        preserve_route = config.expected_project_id is not None
        if request.method in {"GET", "HEAD"}:
            return HTMLResponse(login_page(engineer=engineer, preserve_route=preserve_route) if request.method == "GET" else "")
        if not same_origin(request):
            return JSONResponse({"detail": "동일한 접속 주소에서 로그인해 주세요."}, status_code=403)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            return JSONResponse({"detail": "지원하지 않는 로그인 형식입니다."}, status_code=415)
        now = clock()
        while attempts and attempts[0] <= now - 60:
            attempts.popleft()
        if len(attempts) >= 8:
            return HTMLResponse(login_page("로그인 시도가 많습니다. 1분 뒤 다시 시도해 주세요.", engineer=engineer,
                                           preserve_route=preserve_route), status_code=429,
                                headers={"Retry-After": "60"})
        attempts.append(now)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                return JSONResponse({"detail": "로그인 요청이 너무 큽니다."}, status_code=413)
        try:
            fields = parse_qs(body.decode("utf-8"), max_num_fields=4, strict_parsing=True)
            password = fields.get("password", [""])
            supplied = password[0] if len(password) == 1 else ""
            routes = fields.get("return_hash", [""])
            return_hash = routes[0] if len(routes) == 1 else ""
        except (UnicodeDecodeError, ValueError):
            supplied = ""
            return_hash = ""
        if not secrets.compare_digest(supplied.encode("utf-8"), config.password.encode("utf-8")):
            return HTMLResponse(login_page("비밀번호를 확인해 주세요.", engineer=engineer,
                                           preserve_route=preserve_route, return_hash=return_hash), status_code=401)
        # Bounded process-local sessions; restart revokes all sessions.
        for token, expiry in list(sessions.items()):
            if expiry <= now:
                del sessions[token]
        if len(sessions) >= 1024:
            return JSONResponse({"detail": "접속 세션이 가득 찼습니다. 잠시 뒤 다시 시도해 주세요."}, status_code=503)
        token = secrets.token_urlsafe(32)
        expiry = min(config.expires_at, now + 12 * 60 * 60)
        sessions[token] = expiry
        response = RedirectResponse(login_landing(config.expected_project_id, return_hash), status_code=303)
        response.set_cookie(COOKIE, token, max_age=max(1, int(expiry - now)), secure=True, httponly=True,
                            samesite="strict", path="/")
        return response

    @app.post("/logout")
    async def logout(request: Request):
        if not same_origin(request):
            return JSONResponse({"detail": "동일한 접속 주소에서 로그아웃해 주세요."}, status_code=403)
        sessions.pop(request.cookies.get(COOKIE, ""), None)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response

    async def bounded_json_body(request: Request) -> bytes | Response:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_JSON_BODY:
                return JSONResponse({"detail": "JSON 요청은 8 MiB 이하여야 합니다."}, status_code=413)
            body.extend(chunk)
        if body:
            try:
                def reject_constant(_value):
                    raise ValueError("Non-finite JSON number")
                json.loads(body.decode("utf-8"), parse_constant=reject_constant)
            except (ValueError, RecursionError):
                return JSONResponse({"detail": "유효한 UTF-8 JSON 요청이 필요합니다."}, status_code=400)
        return bytes(body)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
    async def read(request: Request, path: str):
        route = request.url.path
        if route == CONTEXT_PATH:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "다른 사이트에서의 API 요청은 차단됩니다."}, status_code=403)
            value = {"schema_version": "axiom.external-viewer-context/1", "mode": "external_workspace" if engineer else "external_viewer",
                     "read_only": not engineer, "authentication": "gateway_session", "expires_at": config.expires_at}
            if engineer:
                value.update(editing_allowed=True, execution_allowed=True)
            return JSONResponse(value) if request.method == "GET" else Response(media_type="application/json")
        if route.startswith("/api/v1/"):
            writing = request.method in WRITE_METHODS
            pinned = (any(method == request.method and rule.fullmatch(route) for method, rule in approved_writes)
                      if writing else any(rule.fullmatch(route) for rule in approved))
            if (any(rule.fullmatch(route) for rule in BLOCKED_PROBES)
                    or (not engineer and READONLY_REMOTE.fullmatch(route))
                    or (writing and (not engineer or PROJECT_ADMIN_WRITES.fullmatch(route))) or not pinned):
                return JSONResponse({"detail": "외부 조회에서 허용하지 않는 API입니다. 원격 상태 탐사와 새 API는 차단됩니다."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "다른 사이트에서의 API 요청은 차단됩니다."}, status_code=403)
            if writing and not same_origin(request):
                return JSONResponse({"detail": "동일한 접속 주소에서만 수정·실행할 수 있습니다."}, status_code=403)
            if config.expected_project_id is not None and any(
                    value != config.expected_project_id for value in request.query_params.getlist("project_id")):
                return JSONResponse({"detail": "공유된 프로젝트의 요청만 허용됩니다."}, status_code=403)
            if writing:
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if (content_type not in {"", "application/json"}
                        or request.headers.get("content-encoding", "identity").lower() != "identity"):
                    return JSONResponse({"detail": "압축하지 않은 application/json 요청만 지원합니다."}, status_code=415)
                length = request.headers.get("content-length")
                if length is not None and not re.fullmatch(r"[0-9]{1,12}", length):
                    return JSONResponse({"detail": "요청 크기 헤더가 올바르지 않습니다."}, status_code=400)
                if length is not None and int(length) > MAX_JSON_BODY:
                    return JSONResponse({"detail": "JSON 요청은 8 MiB 이하여야 합니다."}, status_code=413)
                key = request.headers.get("idempotency-key")
                if len(request.headers.getlist("idempotency-key")) > 1 or (key is not None and not re.fullmatch(r"[!-~]{1,180}", key)):
                    return JSONResponse({"detail": "Idempotency-Key는 1–180자의 ASCII 식별자여야 합니다."}, status_code=400)
            elif request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers:
                return JSONResponse({"detail": "조회 요청 본문은 허용되지 않습니다."}, status_code=400)
            try:
                await asyncio.wait_for(slots.acquire(), timeout=3)
            except TimeoutError:
                return JSONResponse({"detail": "동시 조회가 많습니다. 잠시 뒤 다시 시도해 주세요."}, status_code=503)
            client = request.app.state.upstream_client
            content = None
            if writing:
                try:
                    content = await asyncio.wait_for(bounded_json_body(request), timeout=10)
                except TimeoutError:
                    slots.release()
                    return JSONResponse({"detail": "JSON 요청 수신 시간이 초과되었습니다."}, status_code=408)
                except BaseException:
                    slots.release()
                    raise
                if isinstance(content, Response):
                    slots.release()
                    return content
                if content and not content_type:
                    slots.release()
                    return JSONResponse({"detail": "JSON 본문에는 application/json 형식이 필요합니다."}, status_code=415)
                if content and config.expected_project_id is not None:
                    document = json.loads(content)
                    if (isinstance(document, dict) and "project_id" in document
                            and document["project_id"] != config.expected_project_id):
                        slots.release()
                        return JSONResponse({"detail": "공유된 프로젝트의 요청만 허용됩니다."}, status_code=403)
            allowed_headers = ("accept", "idempotency-key") if writing else ("accept", "range", "if-range")
            headers = {key: request.headers[key] for key in allowed_headers if key in request.headers}
            if writing:
                headers["content-type"] = "application/json"
            headers["accept-encoding"] = "identity"
            upstream_response = None
            try:
                url = config.upstream + route
                if request.url.query:
                    url += "?" + request.url.query
                outgoing = client.build_request(request.method if writing else "GET", url, headers=headers, content=content)
                # AsyncClient may have collected an upstream Set-Cookie; never replay it.
                for name in ("cookie", "authorization", "proxy-authorization"):
                    outgoing.headers.pop(name, None)
                if upstream_token:
                    outgoing.headers["Authorization"] = "Bearer " + upstream_token
                upstream_response = await client.send(outgoing, stream=True)
            except (httpx.HTTPError, ValueError):
                slots.release()
                return JSONResponse({"detail": "로컬 API에 연결할 수 없습니다."}, status_code=502)
            except BaseException:
                slots.release()
                raise
            if 300 <= upstream_response.status_code < 400:
                await upstream_response.aclose()
                slots.release()
                return JSONResponse({"detail": "API 리디렉션은 허용되지 않습니다."}, status_code=502)
            if upstream_response.status_code >= 500:
                await upstream_response.aclose()
                slots.release()
                return JSONResponse({"detail": "로컬 API에서 조회를 완료하지 못했습니다."}, status_code=502)
            result_headers = {key: upstream_response.headers[key] for key in
                              ("content-type", "content-disposition", "content-range", "accept-ranges", "etag")
                              if key in upstream_response.headers}
            retry_after = upstream_response.headers.get('retry-after', '')
            if upstream_response.status_code == 429 and re.fullmatch(r'[0-9]{1,6}', retry_after):
                result_headers['retry-after'] = retry_after
            # These describe the exact wire representation and are required by
            # managed source downloads. httpx decodes compressed bodies, so do
            # not attach their encoded length/hash to a different representation.
            if upstream_response.headers.get("content-encoding", "identity").lower() == "identity":
                length = upstream_response.headers.get("content-length", "")
                digest = upstream_response.headers.get("x-content-sha256", "")
                if re.fullmatch(r"0|[1-9][0-9]{0,11}", length):
                    result_headers["content-length"] = length
                if re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                    result_headers["x-content-sha256"] = digest
            if request.method == "HEAD":
                await upstream_response.aclose()
                slots.release()
                return Response(status_code=upstream_response.status_code, headers=result_headers)

            closed = False

            async def close_stream():
                nonlocal closed
                if not closed:
                    closed = True
                    try:
                        await upstream_response.aclose()
                    finally:
                        slots.release()

            async def stream():
                try:
                    async for chunk in upstream_response.aiter_bytes():
                        yield chunk
                finally:
                    await close_stream()
            return StreamingResponse(stream(), status_code=upstream_response.status_code, headers=result_headers,
                                     background=BackgroundTask(close_stream))
        current_assets = assets
        if route in current_assets:
            data, media_type = current_assets[route]
            return Response(data if request.method == "GET" else b"", media_type=media_type)
        return JSONResponse({"detail": "공개 대상에 포함되지 않은 파일입니다."}, status_code=404)

    return app


def main():
    parser = argparse.ArgumentParser(description="Authenticated temporary scoped AXIOM gateway")
    parser.add_argument("--config", required=True, type=Path, help="Owner-only 0600 local JSON config")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    try:
        app = create_app(load_config(args.config))
    except (OSError, ValueError, json.JSONDecodeError):
        parser.error("Invalid gateway configuration or production dist (credentials are not logged)")
    # Local process-owner control only. No public reload endpoint, expiry
    # extension, route expansion, credential replacement or session revocation.
    signal.signal(signal.SIGHUP, lambda _signum, _frame: app.state.reload_static_snapshot())
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1", server_header=False)


if __name__ == "__main__":
    main()
