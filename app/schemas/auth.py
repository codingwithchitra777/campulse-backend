from typing import Optional
from pydantic import BaseModel

class GoogleAuthRequest(BaseModel):
    credential: str

class DemoAuthRequest(BaseModel):
    userId: str
    userName: str

class TelegramWebAppAuthRequest(BaseModel):
    """Telegram Mini App login: the raw window.Telegram.WebApp.initData string."""
    initData: str

class TelegramAuthRequest(BaseModel):
    """The Telegram Login Widget's signed user object, verbatim."""
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str
