"""Runs page: every scrape invocation found in the manifests (CLI,
scheduled or — from Phase 3 — the panel's own jobs), and a digest of
run_config.yaml, which drives the next CLI/scheduled run."""
from fastapi import APIRouter, HTTPException, Request

from .common import render, service
from .pages import Page

router = APIRouter()
PAGE = Page('runs', 'Runs', router, nav_order=20)


@router.get('/runs')
def runs(request: Request):
    svc = service(request)
    model = svc.model()
    return render(request, 'runs.html', active='runs',
                  invocations=model.invocations(), run_config=svc.run_config(),
                  model=model, version=svc.status()['version'])


@router.get('/runs/invocation')
def invocation_runs(request: Request, key: str):
    model = service(request).model()
    matches = [i for i in model.invocations() if i.key == key]
    if not matches:
        raise HTTPException(404, f'unknown invocation: {key}')
    return render(request, 'partials/invocation_runs.html',
                  invocation=matches[0], model=model)
