"""Two fake background jobs used to verify that the queue system works."""
import logging
import time

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.test.add_numbers_task")
def add_numbers_task(x: int, y: int) -> int:
    """Add two numbers, slowly."""
    logger.info(f"Executing add_numbers_task with x={x}, y={y}")

    time.sleep(2)

    result = x + y
    logger.info(f"add_numbers_task completed. Result: {result}")

    return result


@celery_app.task(name="app.tasks.test.dummy_process_document_task")
def dummy_process_document_task(document_id: str) -> dict:
    """Pretend to process a document, printing each stage."""
    logger.info(f"Initializing dummy ingestion for document: {document_id}")

    stages = ["Virus Scanning", "Text Extraction", "Chunking", "Vector Embedding"]
    for index, stage in enumerate(stages, 1):
        logger.info(f"[{index}/{len(stages)}] Running {stage}...")
        time.sleep(1)

    logger.info(f"Ingestion completed for document: {document_id}")

    return {
        "document_id": document_id,
        "status": "completed",
        "processed_chunks": 14,
        "embedding_model": "simulated",
    }
