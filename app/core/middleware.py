import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging_config import request_id_ctx

logger = logging.getLogger("app.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id", str(uuid.uuid4()))
        token = request_id_ctx.set(rid)
        start = time.perf_counter()
        client = request.client.host if request.client else "-"
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled method=%s path=%s client=%s", request.method, request.url.path, client)
            raise
        finally:
            request_id_ctx.reset(token)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "method=%s path=%s status=%s duration_ms=%.1f client=%s",
            request.method, request.url.path, response.status_code, duration_ms, client,
        )
        response.headers["x-request-id"] = rid
        return response