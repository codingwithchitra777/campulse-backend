"""Account-linking endpoints. A logged-in (canonical) account mints a one-time
code; the Telegram bot's /start <code> deep link redeems it (see telegram_bot._start).
Also lists and removes linked Telegram accounts."""
import logging
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user, get_link_repo, CurrentUser
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/auth/link/code")
def create_link_code(current_user: CurrentUser = Depends(get_current_user), link_repo = Depends(get_link_repo)):
    """Mint a one-time code + the Telegram deep link the user taps to connect
    their Telegram account to this (primary) account."""
    code = link_repo.create_code(current_user.user_id)
    deep_link = f"https://t.me/{settings.telegram_bot_username}?start={code}"
    return {"success": True, "code": code, "deepLink": deep_link, "botUsername": settings.telegram_bot_username}


@router.get("/auth/links")
def list_links(current_user: CurrentUser = Depends(get_current_user), link_repo = Depends(get_link_repo)):
    return {"success": True, "links": link_repo.list_links(current_user.user_id)}


@router.delete("/auth/links/{alias_user_id}")
def remove_link(alias_user_id: str, current_user: CurrentUser = Depends(get_current_user), link_repo = Depends(get_link_repo)):
    """Disconnect a linked account. Only the primary that owns the link may remove
    it (a linked alias resolves to the primary, so its JWT sub is the primary)."""
    owned = any(l["aliasUserId"] == alias_user_id for l in link_repo.list_links(current_user.user_id))
    if not owned:
        raise HTTPException(status_code=404, detail="Link not found")
    link_repo.remove_link(alias_user_id)
    return {"success": True}
