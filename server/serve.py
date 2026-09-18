"""Async static file server with Range request support for PMTiles.

Uses starlette + uvicorn for proper async I/O and concurrent request handling.
Falls back to threaded http.server if starlette is unavailable.

Configuration via environment variables:
    GIS_VIEWER_PORT     - Server port (default: 3000)
    GIS_VIEWER_BASE_DIR - Base directory to serve from (default: repo root)
    GIS_VIEWER_PASSWORD - Password for HTTP Basic Auth (required)
"""
import base64
import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PORT = int(os.environ.get("GIS_VIEWER_PORT", sys.argv[1] if len(sys.argv) > 1 else 3000))
BASE_DIR = os.environ.get(
    "GIS_VIEWER_BASE_DIR",
    str(Path(__file__).resolve().parent.parent)
)
BASE_DIR = os.path.abspath(BASE_DIR)
PASSWORD = os.environ.get("GIS_VIEWER_PASSWORD")
if not PASSWORD:
    sys.exit(
        "GIS_VIEWER_PASSWORD must be set — there is no default password.\n"
        "Example: GIS_VIEWER_PASSWORD=... python server/serve.py"
    )
AUTH_REALM = "Scout Tree"


def check_password(provided: str) -> bool:
    """Constant-time password comparison."""
    return hmac.compare_digest(provided.encode(), PASSWORD.encode())


def run_starlette():
    """High-performance async server using starlette + uvicorn."""
    import mimetypes
    from starlette.applications import Starlette
    from starlette.responses import Response, FileResponse
    from starlette.routing import Mount, Route
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    import aiofiles
    import uvicorn

    async def serve_range(request):
        """Handle Range requests for PMTiles (and other large files)."""
        path = request.path_params.get("path", "")

        # Serve frontend/index.html at root
        if path == "" or path == "/":
            path = "frontend/index.html"

        file_path = os.path.join(BASE_DIR, path)
        file_path = os.path.normpath(file_path)

        # Security: prevent path traversal
        if not file_path.startswith(BASE_DIR):
            return Response(status_code=403)
        if not os.path.isfile(file_path):
            return Response(status_code=404)

        range_header = request.headers.get("range")
        if not range_header:
            # Non-range request — serve full file
            media_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600"}
            return FileResponse(file_path, media_type=media_type, headers=headers)

        file_size = os.path.getsize(file_path)
        range_spec = range_header.replace("bytes=", "")
        start_str, end_str = range_spec.split("-")
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        async with aiofiles.open(file_path, "rb") as f:
            await f.seek(start)
            data = await f.read(length)

        media_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        }
        return Response(content=data, status_code=206, media_type=media_type, headers=headers)

    class BasicAuthMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()

            authenticated = False
            username = None
            if auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode()
                    # Accept any username, just check the password
                    user, _, pwd = decoded.partition(":")
                    if check_password(pwd):
                        authenticated = True
                        username = user
                except Exception:
                    pass

            if not authenticated:
                response = Response(
                    content="Unauthorized",
                    status_code=401,
                    headers={"WWW-Authenticate": f'Basic realm="{AUTH_REALM}"'},
                )
                await response(scope, receive, send)
                return

            scope["auth_user"] = username or "anonymous"
            await self.app(scope, receive, send)

    # Import collections API routes
    from server.collections_api import collections_routes

    # Catch-all route handles both static files and range requests
    app = Starlette(
        routes=[
            Mount("/api", routes=collections_routes),
            Route("/{path:path}", serve_range),
        ],
        middleware=[
            Middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["Range"],
                       expose_headers=["Content-Range", "Content-Length", "Accept-Ranges"])
        ],
    )
    app = BasicAuthMiddleware(app)

    print(f"Serving Scout Tree at http://0.0.0.0:{PORT} (uvicorn async)")
    print(f"Base directory: {BASE_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


def run_threaded():
    """Fallback threaded server using stdlib."""
    import http.server
    import socketserver

    class RangeHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=BASE_DIR, **kwargs)

        def log_message(self, fmt, *args):
            pass

        def translate_path(self, path):
            """Override to serve frontend/index.html at root."""
            if path == "/" or path == "":
                return os.path.join(BASE_DIR, "frontend", "index.html")
            return super().translate_path(path)

        def end_headers(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Headers', 'Range')
            self.send_header('Access-Control-Expose-Headers', 'Content-Range, Content-Length, Accept-Ranges')
            self.send_header('Accept-Ranges', 'bytes')
            if self.path.endswith('.pmtiles'):
                self.send_header('Cache-Control', 'public, max-age=3600')
            super().end_headers()

        def _check_auth(self):
            """Return True if authenticated, else send 401."""
            auth_header = self.headers.get('Authorization', '')
            if auth_header.startswith('Basic '):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode()
                    _, _, pwd = decoded.partition(':')
                    if check_password(pwd):
                        return True
                except Exception:
                    pass
            self.send_response(401)
            self.send_header('WWW-Authenticate', f'Basic realm="{AUTH_REALM}"')
            self.end_headers()
            self.wfile.write(b'Unauthorized')
            return False

        def do_OPTIONS(self):
            if not self._check_auth():
                return
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            if not self._check_auth():
                return
            rng = self.headers.get('Range')
            if not rng:
                return super().do_GET()
            path = self.translate_path(self.path)
            if not os.path.isfile(path):
                self.send_error(404)
                return
            fs = os.path.getsize(path)
            s, e = rng.replace('bytes=', '').split('-')
            s = int(s) if s else 0
            e = int(e) if e else fs - 1
            e = min(e, fs - 1)
            ln = e - s + 1
            self.send_response(206)
            self.send_header('Content-Type', self.guess_type(path))
            self.send_header('Content-Range', f'bytes {s}-{e}/{fs}')
            self.send_header('Content-Length', str(ln))
            self.end_headers()
            with open(path, 'rb') as f:
                f.seek(s)
                self.wfile.write(f.read(ln))

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    print(f"Serving Scout Tree at http://0.0.0.0:{PORT} (threaded fallback)")
    print(f"Base directory: {BASE_DIR}")
    Server(('0.0.0.0', PORT), RangeHandler).serve_forever()


if __name__ == '__main__':
    try:
        import starlette, uvicorn, aiofiles  # noqa: F401
        run_starlette()
    except ImportError:
        run_threaded()
