"""Preview the case library against an existing local workspace, without restarting it.

Only the new read-only catalog route uses current code and a read-only SQLite
connection. All other API requests, including edits, go to the original service
with the browser's existing bearer header. No worker or database migration runs.
This loopback review tool does not add authentication or bypass existing access.
"""
import argparse
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend' / 'src'))


def review_app(workspace: Path, upstream: str, ui: Path, operator_token_file: Path, curation: Path):
    import httpx
    import sqlite3
    from fastapi import APIRouter, Depends, FastAPI, Request
    from fastapi.security import HTTPBearer
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from starlette.responses import JSONResponse
    from starlette.staticfiles import StaticFiles
    from ade2e.database import Database, Settings
    from ade2e.demo_cases import register_demo_case_routes
    from ade2e.team_access import authenticate_request

    parsed = urlsplit(upstream)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Review upstream must be an explicit http://127.0.0.1:PORT origin')
    database = (workspace / 'platform.db').resolve(strict=True)
    if not (ui / 'index.html').is_file():
        raise ValueError('Build frontend before launching the review')
    curation = curation.resolve(strict=True)
    settings = Settings(data_dir=workspace, api_token=operator_token_file.read_text().strip(), team_mode=True, seed_examples=False)
    # Do not instantiate Database: its normal constructor creates directories.
    class ReadOnlyDatabase:
        read_session = Database.read_session
        experiment_dir = workspace / 'experiments'
        engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(
            database.as_uri() + '?mode=ro', uri=True, check_same_thread=False, timeout=30))
        session = sessionmaker(engine, expire_on_commit=False)
    db = ReadOnlyDatabase()
    overlay = FastAPI()
    bearer = HTTPBearer(auto_error=False)

    def authenticate(request: Request, credentials=Depends(bearer)):
        return authenticate_request(request, db, settings, credentials.credentials if credentials else None)

    api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_demo_case_routes(api, db, curation_path=curation)
    overlay.include_router(api)
    static = StaticFiles(directory=ui, html=True)
    client = httpx.AsyncClient(base_url=upstream.rstrip('/'), timeout=httpx.Timeout(120, connect=5), follow_redirects=False)
    hop = {b'host', b'connection', b'transfer-encoding', b'keep-alive', b'proxy-authenticate', b'proxy-authorization', b'te', b'trailer', b'upgrade'}

    def forwarded_headers(headers):
        # Connection can nominate additional headers that apply to one hop only.
        blocked = hop | {name.strip().lower() for key, value in headers if key.lower() == b'connection'
                         for name in value.split(b',')}
        return [(key, value) for key, value in headers if key.lower() not in blocked]

    async def application(scope, receive, send):
        if scope['type'] == 'lifespan':
            # Deliberately never enter the overlay lifespan: no init/lease writes.
            while True:
                message = await receive()
                if message['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                elif message['type'] == 'lifespan.shutdown':
                    await client.aclose()
                    db.engine.dispose()
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        elif scope['type'] == 'http':
            if scope['path'] == '/api/v1/demo-cases':
                if scope['method'] != 'GET':
                    await JSONResponse({'detail': 'Demo catalog is read-only'}, status_code=405)(scope, receive, send)
                else:
                    await overlay(scope, receive, send)
            elif scope['path'].startswith('/api/') or scope['path'] in ('/openapi.json', '/docs', '/redoc', '/llms.txt'):
                async def request_body():
                    while True:
                        message = await receive()
                        if message['type'] == 'http.disconnect':
                            raise httpx.RequestError('Review client disconnected')
                        yield message.get('body', b'')
                        if not message.get('more_body'):
                            return
                target = scope['raw_path'].decode('ascii') + ('?' + scope['query_string'].decode('ascii') if scope['query_string'] else '')
                headers = forwarded_headers(scope['headers'])
                started = False
                try:
                    async with client.stream(scope['method'], target, headers=headers, content=request_body()) as response:
                        await send({'type': 'http.response.start', 'status': response.status_code,
                                    'headers': forwarded_headers(response.headers.raw)})
                        started = True
                        async for chunk in response.aiter_raw():
                            await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
                        await send({'type': 'http.response.body', 'body': b''})
                except httpx.RequestError:
                    if started:
                        # An interrupted artifact must remain an interrupted body;
                        # a second response or successful EOF would hide failure.
                        raise
                    await JSONResponse({'detail': 'Original workspace is unavailable; the review does not restart it.'}, status_code=502)(scope, receive, send)
            else:
                await static(scope, receive, send)

    return application


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--operator-token-file', type=Path, required=True)
    parser.add_argument('--curation', type=Path, required=True)
    parser.add_argument('--ui', type=Path, default=ROOT / 'frontend' / 'dist')
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    if args.port == urlsplit(args.upstream).port:
        parser.error('Review and original service must use different ports')
    app = review_app(args.workspace, args.upstream, args.ui, args.operator_token_file, args.curation)
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
