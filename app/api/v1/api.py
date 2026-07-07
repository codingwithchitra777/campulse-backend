from fastapi import APIRouter
from app.api.v1.endpoints import auth, market, portfolio, trade

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(market.router)
api_router.include_router(portfolio.router)
api_router.include_router(trade.router)
