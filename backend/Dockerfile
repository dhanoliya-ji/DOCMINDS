FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG EMBEDDING_PROVIDER=local

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    if [ "$EMBEDDING_PROVIDER" = "local" ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    else \
        grep -v "^sentence-transformers" requirements.txt > /tmp/requirements.txt && \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    fi && \
    python -m pip uninstall -y pip

RUN if [ "$EMBEDDING_PROVIDER" = "local" ]; then \
        python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"; \
    fi

COPY . .

RUN mkdir -p /app/uploads

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
