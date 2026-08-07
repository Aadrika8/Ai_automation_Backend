from fastapi import APIRouter, Depends, HTTPException

from app import repositories as repo
from app.models import AppSettings
from app.security import get_current_user, require_role

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=AppSettings, dependencies=[Depends(get_current_user)])
async def get_settings_route():
    doc = await repo.get_settings_doc()
    if doc is None:
        raise HTTPException(status_code=404, detail="Settings not found")
    return doc


@router.put("/settings", response_model=AppSettings, dependencies=[Depends(require_role("admin"))])
async def put_settings_route(body: AppSettings):
    doc = body.model_dump(by_alias=True)
    await repo.put_settings_doc(doc)
    return doc
