"""AI-generated quality report for one app layer. Cached in Mongo by data
hash, so unchanged data never spends OpenAI credits twice."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app import report_ai
from app import repositories as repo
from app.config import get_settings
from app.models import AiReport, AppId, LayerId
from app.security import require_role

router = APIRouter(tags=["reports"], dependencies=[Depends(require_role("qa"))])


@router.post("/apps/{app_id}/layers/{layer_id}/report", response_model=AiReport)
async def generate_layer_report(app_id: AppId, layer_id: LayerId, force: bool = False):
    if not get_settings().openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="Report generation is not configured (set OPENAI_API_KEY in Backend/.env)",
        )

    dashboard = await repo.get_dashboard(app_id, layer_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Layer not found")

    failed = await repo.list_failed_tests(app_id, layer_id)
    payload = report_ai.build_payload(app_id, layer_id, dashboard, failed)
    data_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    cached = await repo.get_cached_report(app_id, layer_id)
    if cached is not None and cached.get("dataHash") == data_hash and not force:
        return {**cached["report"], "cached": True}

    try:
        report = await report_ai.generate_report(payload)
    except Exception as exc:  # OpenAI/network failure -> clean 502, not an opaque 500
        raise HTTPException(status_code=502, detail=f"Report generation failed: {exc}") from exc

    full = {
        **report,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "model": get_settings().openai_model,
        "cached": False,
    }
    await repo.put_cached_report(app_id, layer_id, {"dataHash": data_hash, "report": full})
    return full
