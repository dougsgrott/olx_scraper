"""Localhost-only, no login — but one POST can (from Phase 3) launch a
browser on the home IP, so two cheap guards still apply:

  - TrustedHostMiddleware (app.py) rejects any Host but localhost/127.0.0.1,
    which defeats DNS rebinding (a hostile page re-pointing its own domain
    at 127.0.0.1);
  - OriginGuard refuses state-changing requests that don't come from the
    panel's own pages: a cross-site form POST carries a foreign Origin, and
    a request without Origin must at least be an htmx request.
"""
from starlette.responses import PlainTextResponse

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
ALLOWED_HOSTS = ['localhost', '127.0.0.1']


class OriginGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['method'] not in SAFE_METHODS:
            headers = {k.decode('latin-1').lower(): v.decode('latin-1')
                       for k, v in scope['headers']}
            origin = headers.get('origin')
            host = headers.get('host', '')
            if origin is not None:
                allowed = origin in (f'http://{host}', f'https://{host}')
            else:
                allowed = headers.get('hx-request') == 'true'
            if not allowed:
                response = PlainTextResponse(
                    'Forbidden: request did not come from the panel.', status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
