"""Imports every Celery task, so the worker knows they exist."""
from app.tasks.connectors import run_import_task, sync_connectors_task
from app.tasks.ingestion import process_document_task, recover_stuck_documents_task
from app.tasks.test import add_numbers_task, dummy_process_document_task
from app.tasks.webhooks import deliver_webhook_task, sweep_webhooks_task

__all__ = [
    "process_document_task",
    "recover_stuck_documents_task",
    "add_numbers_task",
    "dummy_process_document_task",
    "deliver_webhook_task",
    "sweep_webhooks_task",
    "sync_connectors_task",
    "run_import_task",
]
