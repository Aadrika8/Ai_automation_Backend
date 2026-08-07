from fastapi import APIRouter, Depends, HTTPException

from app import repositories as repo
from app.models import AppId, AppSummary, LayerDashboard, LayerId, LayerInfo, TestCaseRow
from app.security import get_current_user, require_role

router = APIRouter(tags=["apps"], dependencies=[Depends(get_current_user)])


@router.get("/apps", response_model=list[AppSummary])
async def get_apps():
    return await repo.list_apps()


@router.get("/apps/{app_id}/layers", response_model=list[LayerInfo])
async def get_layers(app_id: AppId):
    layers = await repo.list_layers(app_id)
    if not layers:
        raise HTTPException(status_code=404, detail="Application not found")
    return layers


@router.get("/apps/{app_id}/layers/{layer_id}/dashboard", response_model=LayerDashboard)
async def get_dashboard(app_id: AppId, layer_id: LayerId):
    dashboard = await repo.get_dashboard(app_id, layer_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Layer not found")
    return dashboard


@router.get(
    "/apps/{app_id}/layers/{layer_id}/tests",
    response_model=list[TestCaseRow],
    dependencies=[Depends(require_role("qa"))],
)
async def get_tests(app_id: AppId, layer_id: LayerId):
    return await repo.list_tests(app_id, layer_id)
