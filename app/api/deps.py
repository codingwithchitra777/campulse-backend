from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.repositories.user import UserRepository
from app.services.pricing import pricing_service_instance
from app.services.portfolio import PortfolioService

def get_trade_repo() -> TradeRepository:
    return TradeRepository()

def get_alloc_repo() -> AllocationRepository:
    return AllocationRepository()

def get_user_repo() -> UserRepository:
    return UserRepository()

def get_pricing_service():
    return pricing_service_instance

def get_portfolio_service() -> PortfolioService:
    return PortfolioService(TradeRepository(), AllocationRepository(), pricing_service_instance)
