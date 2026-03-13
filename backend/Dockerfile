FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml .
COPY backend/src/ src/
RUN pip install --no-cache-dir .

COPY backend/alembic.ini .
COPY backend/alembic/ alembic/
COPY shared/ /app/shared/

ENV PYTHONPATH=/app/src:/app

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop"]
