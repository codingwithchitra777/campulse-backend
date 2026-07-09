from pydantic import BaseModel

class GoogleAuthRequest(BaseModel):
    credential: str

class DemoAuthRequest(BaseModel):
    userId: str
    userName: str
