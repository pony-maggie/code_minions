"""Start-run routes: GET /new (page), GET /new/inputs (fragment), POST /new (submit)."""
from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from code_minions.engine.workflow import Workflow, WorkflowLoadError, load_workflow

router = APIRouter()

IGNORED_FILE_DIRS = {".git", ".devflow", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
LOCAL_FILE_SUFFIXES = {".adoc", ".json", ".md", ".markdown", ".rst", ".txt", ".yaml", ".yml"}


def _list_workflow_names() -> list[str]:
    """Enumerate unique workflow stems from configured + builtin search paths."""
    from code_minions.web.deps import workflow_search_paths
    names: list[str] = []
    seen: set[str] = set()
    for base in workflow_search_paths():
        if not base.exists():
            continue
        for p in sorted(base.glob("*.yaml")):
            stem = p.stem
            if stem in seen:
                continue
            seen.add(stem)
            names.append(stem)
    return names


def _load_workflow_by_name(name: str) -> Workflow:
    """Load a workflow spec by name (configured paths first, then builtin)."""
    from code_minions.web.deps import workflow_search_paths
    for base in workflow_search_paths():
        cand = base / f"{name}.yaml"
        if cand.exists():
            return load_workflow(cand)
    raise WorkflowLoadError(f"workflow not found: {name}")


def _is_path_input(name: str, spec_type: str) -> bool:
    return spec_type == "string" and (name == "prd" or name == "file" or name.endswith("_file"))


def _local_file_options(limit: int = 300) -> list[str]:
    from code_minions.web.deps import _project_root
    root = _project_root()
    options: list[str] = []
    for path in sorted(root.rglob("*")):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in IGNORED_FILE_DIRS for part in rel.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in LOCAL_FILE_SUFFIXES:
            continue
        options.append(rel.as_posix())
        if len(options) >= limit:
            break
    return options


def _file_options_for_inputs(inputs: dict[str, Any]) -> dict[str, list[str]]:
    files = _local_file_options()
    return {
        name: files
        for name, spec in inputs.items()
        if _is_path_input(name, spec.type)
    }


@router.get("/new", response_class=HTMLResponse)
async def new_run_page(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "new_run.html",
        {"request": request, "workflow_names": _list_workflow_names()},
    )


@router.get("/new/inputs", response_class=HTMLResponse)
async def new_run_inputs_fragment(request: Request, workflow: str) -> HTMLResponse:
    try:
        wf = _load_workflow_by_name(workflow)
    except WorkflowLoadError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "partials/inputs_form.html",
        {
            "request": request,
            "workflow": workflow,
            "inputs": wf.inputs,
            "file_options": _file_options_for_inputs(wf.inputs),
        },
    )


@router.post("/new")
async def new_run_submit(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    workflow = form.get("workflow")
    if not workflow:
        raise HTTPException(status_code=400, detail="workflow is required")

    try:
        wf = _load_workflow_by_name(workflow)
    except WorkflowLoadError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # Build inputs dict from form fields, respecting declared types.
    inputs: dict[str, Any] = {}
    for key, spec in wf.inputs.items():
        if key not in form:
            if spec.required:
                raise HTTPException(status_code=400, detail=f"input {key!r} is required")
            continue
        raw = form[key]
        t = spec.type
        if t in ("integer", "number"):
            try:
                inputs[key] = int(raw) if t == "integer" else float(raw)
            except ValueError as ex:
                raise HTTPException(status_code=400, detail=f"{key}: expected {t}") from ex
        elif t == "object":
            try:
                inputs[key] = _json.loads(raw)
            except _json.JSONDecodeError as ex:
                raise HTTPException(status_code=400, detail=f"{key}: invalid JSON") from ex
        else:
            inputs[key] = raw

    from code_minions.web.background import start_run_in_background
    from code_minions.web.deps import get_engine, get_store
    store = get_store()
    engine = get_engine()
    run_id = store.create_run(workflow=wf.name, inputs=inputs, llm=engine.llm_display)
    background_tasks.add_task(start_run_in_background, engine, run_id, wf.name, inputs)

    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)
