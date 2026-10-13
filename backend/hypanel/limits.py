import os
from fastapi import HTTPException
from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    """Limit the body before Starlette can spool an unbounded multipart upload."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = int(os.getenv("HYPANEL_UPLOAD_LIMIT_GB", "16")) * 1024**3 + 1024**2
        if not (scope["path"].endswith(("/files/upload", "/import"))):
            limit = 4 * 1024**2
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Ungültige Content-Length"}, status_code=400)(scope, receive, send)
        if length > limit:
            return await JSONResponse({"detail": "Anfrage überschreitet das Upload-Limit"}, status_code=413)(scope, receive, send)
        consumed = 0
        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                raise HTTPException(413, "Anfrage überschreitet das Upload-Limit")
            return message
        await self.app(scope, bounded_receive, send)
