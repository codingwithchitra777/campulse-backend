import os
from fastapi import FastAPI
import logfire

app = FastAPI()

# FastAPI Cloud injects this when the Logfire integration is connected.
logfire.configure(token=os.environ["LOGFIRE_TOKEN"])
logfire.instrument_fastapi(app)

@app.get("/")
async def read_root():
    logfire.info("Saying hello from first-app")
    return {"Hello": "World"}

@app.get("/hello")
async def hello(name: str = "world"):
    logfire.info("Saying hello", name=name)
    return {"message": f"hello {name}"}
