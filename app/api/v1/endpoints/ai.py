"""AI Coach endpoints.

GET  /api/ai/insights  — cached insight, never calls the model.
POST /api/ai/insights  — regenerate; rate-limited and skipped when nothing changed.
GET  /api/ai/health    — admin-only live key check (makes one real, tiny call).
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import (get_current_user, require_admin, get_analytics_service,
                          get_ai_insight_repo, get_ai_coach_service)
from app.services import ai_coach

logger = logging.getLogger(__name__)
router = APIRouter()

# One paid regenerate per user per day. The snapshot-hash check below means an
# unchanged portfolio doesn't even cost that.
REFRESH_INTERVAL = timedelta(days=1)


def _payload(cached, *, stale: bool):
    return {
        "insight": cached["insight"],
        "model": cached["model"],
        "generatedAt": cached["generatedAt"],
        "stale": stale,
        "disclaimer": ai_coach.DISCLAIMER,
    }


@router.get("/ai/insights")
def get_insight(
    current_user = Depends(get_current_user),
    analytics = Depends(get_analytics_service),
    insight_repo = Depends(get_ai_insight_repo),
):
    """Read-only. Returns the cached insight and whether the user's numbers have
    moved since it was written, so the UI can offer a refresh."""
    try:
        if not ai_coach.is_configured():
            return {"enabled": False, "insight": None, "disclaimer": ai_coach.DISCLAIMER}
        cached = insight_repo.get(current_user.user_id)
        if not cached:
            return {"enabled": True, "insight": None, "stale": True,
                    "disclaimer": ai_coach.DISCLAIMER}
        snapshot = ai_coach.build_snapshot(analytics.compute(current_user.user_id))
        stale = ai_coach.snapshot_hash(snapshot) != cached["snapshotHash"]
        return {"enabled": True, **_payload(cached, stale=stale)}
    except Exception as e:
        logger.error(f"Error in get_insight: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai/insights")
def refresh_insight(
    current_user = Depends(get_current_user),
    analytics = Depends(get_analytics_service),
    insight_repo = Depends(get_ai_insight_repo),
    coach = Depends(get_ai_coach_service),
):
    try:
        if not ai_coach.is_configured():
            raise HTTPException(status_code=503, detail="The AI coach is not configured.")

        snapshot = ai_coach.build_snapshot(analytics.compute(current_user.user_id))
        digest = ai_coach.snapshot_hash(snapshot)
        cached = insight_repo.get(current_user.user_id)

        # Nothing changed => serve the cache. Free, and the honest answer.
        if cached and cached["snapshotHash"] == digest:
            return {"enabled": True, "regenerated": False, **_payload(cached, stale=False)}

        # Changed, but too soon to pay again.
        if cached and datetime.utcnow() - cached["generatedAt"] < REFRESH_INTERVAL:
            retry_at = cached["generatedAt"] + REFRESH_INTERVAL
            raise HTTPException(
                status_code=429,
                detail=f"The coach refreshes once a day. Try again after {retry_at.isoformat()}Z."
            )

        try:
            text = coach.generate(snapshot)
        except Exception as e:
            logger.error(f"AI coach generation failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="The AI coach is unavailable right now.")

        insight_repo.upsert(current_user.user_id, digest, text, coach.model)
        return {"enabled": True, "regenerated": True,
                **_payload(insight_repo.get(current_user.user_id), stale=False)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in refresh_insight: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ai/health")
def ai_health(
    _admin = Depends(require_admin),
    coach = Depends(get_ai_coach_service),
):
    """Admin-only. Proves ANTHROPIC_API_KEY works on the deployed box by making
    one real ~$0.0003 call. Distinguishes 'no key', 'key rejected', and 'key fine
    but the account has no credit' — the three failures worth telling apart."""
    if not ai_coach.is_configured():
        return {"configured": False, "ok": False,
                "detail": "ANTHROPIC_API_KEY is not set in this environment."}
    try:
        resp = coach._get_client().messages.create(
            model=coach.model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        )
        usage = resp.usage
        return {"configured": True, "ok": True, "model": resp.model,
                "inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens}
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error(f"AI health check failed: {e}", exc_info=True)
        return {"configured": True, "ok": False, "status": status,
                "detail": getattr(e, "message", str(e))}
