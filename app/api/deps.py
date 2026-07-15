import jwt
from fastapi import Depends, Header, HTTPException, status

from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.repositories.user import UserRepository
from app.repositories.link import LinkRepository
from app.repositories.manual_price import ManualPriceRepository
from app.repositories.watchlist import WatchlistRepository
from app.repositories.alert import AlertRepository
from app.services.pricing import pricing_service_instance
from app.services.portfolio import PortfolioService
from app.services.price_providers import price_router
from app.core.security import decode_access_token


class CurrentUser:
    def __init__(self, user_id: str, role: str):
        self.user_id = user_id
        self.role = role


def get_current_user(authorization: str = Header(default=None)) -> CurrentUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization[len("Bearer "):]
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return CurrentUser(user_id=payload["sub"], role=payload.get("role", "user"))


def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return current_user


def get_trade_repo() -> TradeRepository:
    return TradeRepository()

def get_alloc_repo() -> AllocationRepository:
    return AllocationRepository()

def get_user_repo() -> UserRepository:
    return UserRepository()

def get_link_repo() -> LinkRepository:
    return LinkRepository()

def get_manual_price_repo() -> ManualPriceRepository:
    return ManualPriceRepository()

def get_watchlist_repo() -> WatchlistRepository:
    return WatchlistRepository()

def get_alert_repo() -> AlertRepository:
    return AlertRepository()

def get_pricing_service():
    return pricing_service_instance

def get_price_router():
    return price_router

def get_portfolio_service() -> PortfolioService:
    return PortfolioService(TradeRepository(), AllocationRepository(), pricing_service_instance,
                            price_router=price_router)

def get_analytics_service():
    from app.services.analytics import AnalyticsService
    return AnalyticsService(TradeRepository(), AllocationRepository(), get_portfolio_service())
