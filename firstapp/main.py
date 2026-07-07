import os

import logfire # Trigger redeploy test 2
from fastapi import FastAPI

app = FastAPI()

token = os.getenv("LOGFIRE_TOKEN")

if token:
    logfire.configure(token=token)
    logfire.instrument_fastapi(app)
else:
    print("LOGFIRE_TOKEN not configured.")

@app.get("/")
async def root():
    logfire.info("Root endpoint called")
    return {"Hello": "World"}

@app.get("/hello")
async def hello(name: str = "world"):
    logfire.info("Hello endpoint", name=name)
    return {"message": f"hello {name}"}