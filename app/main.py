import os
import sys
import logging
import asyncio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from starlette.exceptions import HTTPException as StarletteHTTPException
from dotenv import load_dotenv
import logfire

load_dotenv()

# Configure Logfire first thing so we get runtime logs in the FastAPI Cloud console
logfire_token = os.getenv("LOGFIRE_TOKEN")
if logfire_token:
    try:
        logfire.configure(token=logfire_token, service_name="campulse-backend")
    except Exception as e:
        print(f"Logfire configuration failed: {e}", file=sys.stderr)

from app.core.logging_config import configure_logging
configure_logging()

from app.core.middleware import RequestLoggingMiddleware
from app.api.v1.api import api_router

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Re-assert logging config here too, in case fastapi-cli/uvicorn
    # reconfigured logging after server startup and won the race against
    # the import-time configure_logging() call in main.py.
    configure_logging()
    # Daily Telegram session reminders (no-op unless TELEGRAM_WEBHOOK_SECRET set).
    from app.services.reminder_scheduler import start_reminder_scheduler
    start_reminder_scheduler()
    # Price-alert checker (also a no-op unless the Telegram bot is configured).
    from app.services.alert_service import start_alert_scheduler
    start_alert_scheduler()
    yield
    logger.info("shutdown: fastapi app stopping")


app = FastAPI(
    title="CamPulse API",
    version="1.0.0",
    lifespan=lifespan
)

if logfire_token:
    try:
        logfire.instrument_fastapi(app)
    except Exception as e:
        logger.warning(f"Logfire instrumentation failed: {e}")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom Request Logging Middleware
app.add_middleware(RequestLoggingMiddleware)

# Custom validation error handler to return clean JSON structure
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.warning(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )

# Standard HTTP exception handler
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# Include API Router
app.include_router(api_router, prefix="/api")

# Base health check
@app.get("/api/healthz")
async def health_check():
    return {"status": "healthy", "service": "campulse-backend"}

# Serve static assets (JS, CSS, images) from frontend/dist/frontend/browser/
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist", "frontend", "browser"))
if os.path.exists(frontend_dir):
    app.mount("/assets", StaticFiles(directory=frontend_dir), name="assets")
    
    # Catch-all route to serve Angular's index.html for client-side routing
    @app.get("/{catchall:path}")
    async def serve_spa(catchall: str):
        if catchall.startswith("api/"):
            return {"detail": "Not Found"}
        
        # Check if requested file exists locally in the build directory
        file_path = os.path.join(frontend_dir, catchall)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
            
        # Otherwise, serve index.html for Angular routing
        index_path = os.path.join(frontend_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"detail": "Frontend files not found"}
else:
    @app.get("/")
    async def root_fallback():
        return {"message": "CamPulse API is active. Frontend build directory not found."}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
