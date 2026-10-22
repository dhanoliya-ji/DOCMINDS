"""
test.py  (diagnostic Celery tasks)
==================================
WHAT THIS FILE DOES
-------------------
Two fake background jobs used to verify that the queue system works.

Neither does anything real: no files are read, no database is touched, no AI is
called. They exist purely to answer one question:

        "Is the worker actually running and picking up jobs?"

WHY THAT QUESTION IS WORTH A DEDICATED TOOL
-------------------------------------------
The single most common setup problem with this project is documents sitting on
"Queued" forever. There are several possible causes -- the worker was never
started, Redis is not running, the worker cannot reach Redis, the task was
never registered -- and from the outside they all look identical.

Running `POST /tasks/trigger-add` separates them instantly:

    SUCCESS       the broker, the worker and the result backend are all fine,
                  so the fault is inside the ingestion pipeline itself.
    PENDING       nothing is consuming the queue: no worker, or no Redis.

That halves the search space in one request. The real pipeline lives in
`tasks/ingestion.py`; nothing here is used in normal operation.
"""

import logging
import time

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.test.add_numbers_task")
def add_numbers_task(x: int, y: int) -> int:
    """
    Add two numbers, slowly.

    The `name=` argument pins the task's identity. Without it Celery derives a
    name from the module path, so moving or renaming this file would strand any
    jobs already sitting in the queue under the old name.

    The deliberate 2-second pause makes the asynchronous behaviour visible: the
    HTTP response returns immediately, and you can watch the status move from
    PENDING through STARTED to SUCCESS by polling /tasks/status/{id}.
    """
    logger.info(f"Executing add_numbers_task with x={x}, y={y}")

    # Simulated work. In a real task this would be the slow part.
    time.sleep(2)

    result = x + y
    logger.info(f"add_numbers_task completed. Result: {result}")

    # The return value is stored in the result backend (Redis) and is what
    # /tasks/status/{id} reports back as `result`.
    return result


@celery_app.task(name="app.tasks.test.dummy_process_document_task")
def dummy_process_document_task(document_id: str) -> dict:
    """
    Pretend to process a document, printing each stage.

    Mirrors the SHAPE of the real ingestion pipeline without doing any of the
    work, which makes it useful for confirming that a longer multi-step job
    runs to completion and that its log output is visible in the worker
    terminal.

    The real implementation is `process_document_task` in tasks/ingestion.py,
    and it is triggered automatically by the upload endpoint.
    """
    logger.info(f"Initializing dummy ingestion for document: {document_id}")

    stages = ["Virus Scanning", "Text Extraction", "Chunking", "Vector Embedding"]
    for index, stage in enumerate(stages, 1):
        logger.info(f"[{index}/{len(stages)}] Running {stage}...")
        time.sleep(1)

    logger.info(f"Ingestion completed for document: {document_id}")

    # Plausible-looking but entirely invented numbers. Nothing here was
    # measured, which is exactly why this task is confined to diagnostics.
    return {
        "document_id": document_id,
        "status": "completed",
        "processed_chunks": 14,
        "embedding_model": "simulated",
    }
