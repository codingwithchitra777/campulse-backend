import os
import sys
import logging
import asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

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
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
