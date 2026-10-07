"""Request and JSON limits applied before persistence and audit serialization."""
import json
from fastapi import HTTPException
from starlette.responses import JSONResponse

KIB = 1024
MIB = 1024 * KIB
SETTINGS_BYTES = 256 * KIB
PREFERENCES_BYTES = 16 * KIB


class BodyLimitMiddleware:
    """Count streamed bytes, including chunked requests; never pre-buffer bodies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        limit = 15 * MIB if path == "/api/export/png" else 6 * MIB if path == "/api/file/import-csv" else MIB
        if path == "/api/settings":
            limit = 384 * KIB
        headers = dict(scope["headers"])
        try:
            size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
        if size < 0 or size > limit or len(scope.get("query_string", b"")) > 8192:
            return await JSONResponse({"detail": "Request exceeds size limit"}, status_code=413)(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "Request exceeds size limit")
            return message

        await self.app(scope, bounded_receive, send)


def bounded_json(value, limit=SETTINGS_BYTES):
    # Limits cover unknown keys as well as recognized settings. Check shape
    # iteratively so deeply nested payloads fail without Python recursion errors.
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > 12 or nodes > 20000:
            raise HTTPException(422, "Settings are too complex")
        if isinstance(item, str) and (len(item) > 4096 or "\x00" in item):
            raise HTTPException(422, "Settings text is invalid or exceeds 4096 characters")
        if isinstance(item, dict):
            if any(len(key) > 200 for key in item):
                raise HTTPException(422, "Settings key exceeds 200 characters")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid settings JSON")
    if len(encoded.encode("utf-8")) > limit:
        raise HTTPException(413, "Settings exceed storage size limit")
    return encoded
