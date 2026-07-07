"""Single source of truth for logging - call configure_logging() at process
start AND again at the top of lifespan startup, since the FastAPI CLI
reconfigures uvicorn's logging after server startup and can otherwise win
the race against an import-time logging.basicConfig() call."""
import json
import logging
import os
import sys
from contextvars import ContextVar

import logfire

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    # Block-buffered stdout under a container pipe is why low-volume apps
    # show no runtime logs for long stretches, or only in bursts. Some
    # platforms wrap stdout/stderr in a non-TextIOWrapper stream to capture
    # runtime logs themselves, where .reconfigure() doesn't exist or raises -
    # that must never crash app startup, so it's best-effort only.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    use_json = os.getenv("LOG_FORMAT", "text").lower() == "json"

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        JsonFormatter()
        if use_json
        else logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)

    # logfire.configure() only sends what's explicitly sent through its own
    # SDK (e.g. spans from instrument_fastapi) - stdlib `logging` calls need
    # this handler too, or they never reach Logfire (FastAPI Cloud's Runtime
    # Logs view reads from Logfire, not raw stdout).
    try:
        root.addHandler(logfire.LogfireLoggingHandler())
    except Exception:
        pass

    root.setLevel(level)

    # Route uvicorn's loggers through the same handler instead of letting
    # its own dictConfig install a second, differently-formatted handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # RequestLoggingMiddleware (app.access) already logs every request with
    # duration + request_id - silence uvicorn's own access log to avoid
    # duplicate, less informative lines for the same request.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)