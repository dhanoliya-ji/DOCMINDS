"""
tasks.py
========
WHAT THIS FILE DOES
-------------------
Endpoints for inspecting the background job system.

    POST /tasks/trigger-add         run a trivial test job
    POST /tasks/trigger-document    run a simulated ingestion job
    GET  /tasks/status/{task_id}    check what happened to a job

WHY THESE EXIST
---------------
They are DIAGNOSTIC endpoints, not product features.

The most common thing to go wrong when setting this project up is that
documents sit on "Queued" forever. That happens when the Celery worker is not
running, or cannot reach Redis -- and from the outside it looks identical to
the app simply being broken.

`/tasks/trigger-add` isolates the question. It queues a job that adds two
numbers. If the status becomes SUCCESS, the whole broker/worker chain is
healthy and the problem lies in the ingestion pipeline itself. If it stays
PENDING forever, no worker is listening. That one test cuts the search space in
half immediately.

WHAT THE STATUS VALUES MEAN
---------------------------
    PENDING  queued, or -- confusingly -- unknown. Celery cannot tell the
             difference between "waiting" and "no such task", because both
             simply mean "no result recorded".
    STARTED  a worker has picked it up. Only reported because we enabled
             `task_track_started` in core/celery_app.py.
    SUCCESS  finished; `result` holds the return value.
    FAILURE  raised an exception; `result` holds the error.
    RETRY    failed and is being attempted again.
"""

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.celery_app import celery_app
from app.core.config import settings
from app.tasks.test import add_numbers_task, dummy_process_document_task


def _require_worker() -> None:
    """These endpoints exercise the Celery queue, which inline mode does not run.

    Without this, a `.delay()` would wait on a Redis that is not there and the
    request would hang; a clear 503 says what is going on instead.
    """
    if settings.INGESTION_MODE != "celery":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No background worker in this deployment (INGESTION_MODE=inline).",
        )


router = APIRouter(dependencies=[Depends(_require_worker)])


@router.post("/trigger-add", status_code=status.HTTP_202_ACCEPTED)
async def trigger_add_task(x: int, y: int):
    """
    Queue a job that adds two numbers after a short pause.

    The simplest possible end-to-end proof that the queue works: API -> Redis
    -> worker -> result -> API. Use it first whenever background processing
    appears to be stuck.
    """
    # `.delay()` does not run the function. It puts a message on Redis and
    # returns a handle immediately -- which is why the response is 202 Accepted
    # rather than 200 OK.
    task = add_numbers_task.delay(x, y)

    # The id is how you look the result up afterwards.
    return {"task_id": task.id, "status": task.status}


@router.post("/trigger-document", status_code=status.HTTP_202_ACCEPTED)
async def trigger_document_task(document_id: str):
    """
    Queue a SIMULATED document pipeline.

    This runs the fake task in tasks/test.py, which just prints each stage and
    sleeps. It touches no files and no database, so it verifies that a
    multi-step job runs to completion without needing a real upload.

    The REAL pipeline is `process_document_task` in tasks/ingestion.py, and it
    is triggered automatically by the upload endpoint -- not from here.
    """
    task = dummy_process_document_task.delay(document_id)
    return {"task_id": task.id, "status": task.status}


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """
    Look up the current state of a queued job.

    `AsyncResult` reads the job's status from the Celery result backend --
    Redis, in our configuration. It does not talk to the worker directly, which
    is why a task whose result has expired (we set a 1-day TTL) reports as
    PENDING again.
    """
    task_result = AsyncResult(task_id, app=celery_app)

    response = {
        "task_id": task_id,
        "status": task_result.status,
        "result": None,
        "error": None,
    }

    if task_result.status == "SUCCESS":
        # Whatever the task function returned.
        response["result"] = task_result.result
    elif task_result.status == "FAILURE":
        # On failure, `.result` holds the EXCEPTION object rather than a value,
        # so it is cast to a string to keep the response JSON-serialisable.
        response["error"] = str(task_result.result)

    return response
