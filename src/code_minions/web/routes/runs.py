"""Routes for browsing and controlling runs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from code_minions.web.deps import get_store

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def runs_list(request: Request) -> HTMLResponse:
    store = get_store()
    runs = store.list_runs(limit=30)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "runs_list.html",
        {"request": request, "runs": runs},
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: str) -> HTMLResponse:
    store = get_store()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    steps = store.list_steps(run_id)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "run_detail.html",
        {"request": request, "run": run, "steps": steps},
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str):
    store = get_store()
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    from code_minions.web.deps import get_engine
    engine = get_engine()
    engine.cancel_run(run_id)
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@router.post("/runs/{run_id}/resume")
async def resume_run(request: Request, run_id: str):
    store = get_store()
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    from code_minions.engine.engine import EngineError
    from code_minions.web.deps import get_engine
    engine = get_engine()
    try:
        engine.resume_run(run_id)
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)
