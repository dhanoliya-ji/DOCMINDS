"""Endpoints for inspecting the background job system."""
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, status

from app.api import deps

from app.core.celery_app import celery_app
from app.core.config import settings
from app.tasks.test import add_numbers_task, dummy_process_document_task


def _require_worker() -> None:
    """These endpoints exercise the Celery queue, which inline mode does not run."""
    if settings.INGESTION_MODE != "celery":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No background worker in this deployment (INGESTION_MODE=inline).",
        )


def _not_in_production() -> None:
    """Hide the diagnostics entirely in production (404, not 403: there is"""
    if settings.ENV == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(
    dependencies=[
        Depends(_not_in_production),
        Depends(deps.require_admin),
        Depends(_require_worker),
    ]
)


@router.post("/trigger-add", status_code=status.HTTP_202_ACCEPTED)
async def trigger_add_task(x: int, y: int):
    """Queue a job that adds two numbers after a short pause."""
    task = add_numbers_task.delay(x, y)

    return {"task_id": task.id, "status": task.status}


@router.post("/trigger-document", status_code=status.HTTP_202_ACCEPTED)
async def trigger_document_task(document_id: str):
    """Queue a SIMULATED document pipeline."""
    task = dummy_process_document_task.delay(document_id)
    return {"task_id": task.id, "status": task.status}


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """Look up the current state of a queued job."""
    task_result = AsyncResult(task_id, app=celery_app)

    response = {
        "task_id": task_id,
        "status": task_result.status,
        "result": None,
        "error": None,
    }

    if task_result.status == "SUCCESS":
        response["result"] = task_result.result
    elif task_result.status == "FAILURE":
        response["error"] = str(task_result.result)

    return response
