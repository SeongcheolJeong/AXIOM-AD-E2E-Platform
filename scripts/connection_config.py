"""Load the workspace's explicit local CARLA connection without executing shell files."""
import json
from pathlib import Path
from urllib.parse import urlsplit


def carla_environment(root: Path, environment: dict[str, str]) -> dict[str, str]:
    # A caller selecting another endpoint must never inherit this saved endpoint's token.
    if "ADE2E_CARLA_WORKER_URL" in environment:
        return dict(environment)
    runtime = (root / ".runtime").resolve()
    path = runtime / "carla-connection.json"
    if not path.exists():
        return dict(environment)
    try:
        if path.stat().st_size > 4096:
            raise ValueError("oversize")
        config = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(config, dict) or set(config) != {"schema_version", "worker_url", "token_file"}
                or type(config["schema_version"]) is not int or config["schema_version"] != 1):
            raise ValueError("schema")
        url = config["worker_url"]
        if not isinstance(url, str) or not url or len(url) > 2000 or any(c.isspace() for c in url):
            raise ValueError("url")
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("url")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("port")
        relative = config["token_file"]
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("token path")
        token_path = (runtime / relative).resolve()
        if not token_path.is_relative_to(runtime) or token_path.stat().st_size > 1024:
            raise ValueError("token path")
        token = token_path.read_text(encoding="utf-8").strip()
        if not 32 <= len(token) <= 1024 or any(c.isspace() for c in token):
            raise ValueError("token")
        return {**environment, "ADE2E_CARLA_WORKER_URL": url.rstrip("/"),
                "ADE2E_CARLA_WORKER_TOKEN": environment.get("ADE2E_CARLA_WORKER_TOKEN", token)}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Parser/file errors may contain token material or a credential-bearing URL.
        raise ValueError("Saved CARLA connection is invalid. Check .runtime/carla-connection.json and its local token file.") from None
