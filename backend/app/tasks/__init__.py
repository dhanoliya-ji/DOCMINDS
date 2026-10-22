"""
tasks/__init__.py
=================
WHAT THIS FILE DOES
-------------------
Imports every Celery task, so the worker knows they exist.

WHY THIS IS REQUIRED, NOT OPTIONAL
----------------------------------
A Celery worker can only run a task it has REGISTERED, and a task registers
itself when the module defining it is imported. Python never scans folders
looking for functions -- code that is not imported does not exist.

If a task is missing from this list, queueing it appears to work (the message
lands on Redis perfectly happily) and then the worker rejects it with:

        Received unregistered task of type 'app.tasks.ingestion...'

which is a genuinely misleading error, because the function is plainly sitting
right there in the source.

This file is imported by the worker because `core/celery_app.py` sets
`imports=("app.tasks",)`.
"""

from app.tasks.ingestion import process_document_task, recover_stuck_documents_task
from app.tasks.test import add_numbers_task, dummy_process_document_task

# `__all__` documents the public surface of this package and tells linters that
# these imports are intentional re-exports rather than unused leftovers.
__all__ = [
    # The real pipeline: extract -> chunk -> embed -> store.
    "process_document_task",
    # The periodic sweep that re-queues documents orphaned by a dead worker,
    # sent on a timer by Celery beat (see core/celery_app.py).
    "recover_stuck_documents_task",
    # Diagnostic tasks, used by the /tasks endpoints to prove the queue works.
    "add_numbers_task",
    "dummy_process_document_task",
]
