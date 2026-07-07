import os
import sys
import logging
import asyncio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from starlette.exceptions import HTTPException as StarletteHTTPException
from dotenv import load_dotenv

import logfire

load_dotenv()

from app.core.logging_config import configure_logging

# Configure logging before any other app import, so a crash during those
# imports still gets logged instead of failing completely silently.
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
    yield
    logger.info("shutdown: fastapi app stopping")


app = FastAPI(
    title="CamPulse API",
    version="1.0.0",
    lifespan=lifespan
)

# Never let a Logfire outage/misconfig (bad token, blocked egress, etc.)
# take the whole app down - stdout logging via configure_logging() above
# already works independently of this.
try:
    logfire.configure(token=os.getenv("LOGFIRE_TOKEN"), service_name="campulse-backend")
    logfire.instrument_fastapi(app)
except Exception:
    logger.exception("logfire setup failed, continuing without it")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    logger.warning("http_exception status=%s detail=%s path=%s", exc.status_code, exc.detail, request.url.path)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.warning("validation_error path=%s errors=%s", request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("unhandled_exception path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# Include unified API router
app.include_router(api_router, prefix="/api")

# Base health check
@app.get("/api/healthz")
async def health_check():
    return {"status": "healthy", "service": "campulse-backend"}

# Serve static assets (JS, CSS, images) from frontend/dist/frontend/browser/
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist", "frontend", "browser"))
if os.path.exists(frontend_dir):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    
    # Mount browser build directory
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
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
