from fastapi import APIRouter
from app.api.v1.endpoints import admin, auth, deploy, link, market, portfolio, telegram, trade, watchlist

api_router = APIRouter()
api_router.include_router(admin.router)
api_router.include_router(auth.router)
api_router.include_router(deploy.router)
api_router.include_router(link.router)
api_router.include_router(market.router)
api_router.include_router(portfolio.router)
api_router.include_router(telegram.router)
api_router.include_router(trade.router)
api_router.include_router(watchlist.router)
