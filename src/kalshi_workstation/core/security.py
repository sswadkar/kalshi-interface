"""Browser request protection for an unauthenticated, loopback-only trading server.

Any web page open in the operator's browser can send requests to 127.0.0.1. Two
layers stop that from placing orders or changing settings:

- Host checking (Starlette's TrustedHostMiddleware, wired in app.py) rejects
  requests addressed to other host names, which blocks DNS rebinding.
- CrossOriginProtection rejects state-changing requests that a browser marks as
  coming from another site. Requests without browser headers (curl, scripts, the
  test client) are allowed; the server must stay bound to loopback.

Typed JSON request bodies add a third check: FastAPI only parses a body as JSON
when the request declares a JSON content type.
"""
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CrossOriginProtection:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS or _same_origin(scope):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"error": "Cross-origin request rejected", "code": "cross_origin_rejected"},
            status_code=403)
        await response(scope, receive, send)


def _same_origin(scope) -> bool:
    headers = {key.decode("latin-1").lower(): value.decode("latin-1")
               for key, value in scope["headers"]}
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        # "none" is a user-initiated navigation (typed URL, bookmark).
        return fetch_site in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return True  # not sent by a browser
    if origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == headers.get("host", "").lower()
