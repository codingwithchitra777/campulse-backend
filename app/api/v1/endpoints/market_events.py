import logging
import uuid
from typing import List, Optional
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.api.deps import require_admin, get_market_event_repo

logger = logging.getLogger(__name__)
router = APIRouter()

class MarketEventCreate(BaseModel):
    market: str
    eventType: str # 'holiday' | 'dividend'
    eventDate: date
    symbol: Optional[str] = None
    description: Optional[str] = None

class MarketEventResponse(BaseModel):
    eventId: str
    market: str
    eventType: str
    symbol: Optional[str]
    eventDate: date
    description: Optional[str]
    createdBy: Optional[str]

@router.post("/admin/market-events", response_model=MarketEventResponse)
def create_market_event(
    payload: MarketEventCreate,
    current_admin = Depends(require_admin),
    repo = Depends(get_market_event_repo)
):
    try:
        event_id = str(uuid.uuid4())
        repo.upsert_event(
            event_id=event_id,
            market=payload.market,
            event_type=payload.eventType,
            event_date=payload.eventDate,
            symbol=payload.symbol,
            description=payload.description,
            created_by=current_admin.user_id
        )
        return {
            "eventId": event_id,
            "market": payload.market,
            "eventType": payload.eventType,
            "symbol": payload.symbol,
            "eventDate": payload.eventDate,
            "description": payload.description,
            "createdBy": current_admin.user_id
        }
    except Exception as e:
        logger.error(f"Error creating market event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create market event")

@router.delete("/admin/market-events/{event_id}")
def delete_market_event(
    event_id: str,
    current_admin = Depends(require_admin),
    repo = Depends(get_market_event_repo)
):
    try:
        repo.delete_event(event_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting market event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete market event")

@router.get("/market-events")
def get_market_events(
    repo = Depends(get_market_event_repo)
):
    try:
        events = repo.list_events(limit=500)
        return {"success": True, "items": events}
    except Exception as e:
        logger.error(f"Error fetching market events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch market events")
