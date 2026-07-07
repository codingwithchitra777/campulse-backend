import os
import sys
import logging
import asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
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

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield
    logger.info("shutdown: fastapi app stopping")

app = FastAPI(
    title="CamPulse API (Debug Mode)",
    version="1.0.0",
    lifespan=lifespan
)

if logfire_token:
    try:
        logfire.instrument_fastapi(app)
    except Exception as e:
        logger.warning(f"Logfire instrumentation failed: {e}")

# 1. Base health check (No database connection required)
@app.get("/api/healthz")
async def health_check():
    return {"status": "healthy", "service": "campulse-backend-debug"}

# 2. Simple root fallback
@app.get("/")
async def root():
    return {"message": "Debug app started successfully!"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
