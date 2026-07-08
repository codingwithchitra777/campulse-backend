import logging
import requests
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

SERVICE_LABELS = {
    "web": "campulse-web",
    "backend": "campulse-backend",
}


class DeployNotifyRequest(BaseModel):
    service: str
    commit: str


@router.post("/internal/deploy-notify")
def deploy_notify(payload: DeployNotifyRequest, x_deploy_secret: str = Header(default="")):
    if not settings.deploy_notify_secret or x_deploy_secret != settings.deploy_notify_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    label = SERVICE_LABELS.get(payload.service)
    if not label:
        raise HTTPException(status_code=400, detail="Invalid service")

    text = f"\U0001F680 {label} deployed successfully (commit {payload.commit[:7]})"

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
            json={"chat_id": settings.telegram_group_chat_id, "text": text},
            timeout=10,
        )
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Telegram deploy notification: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to notify Telegram")

    return {"success": True}