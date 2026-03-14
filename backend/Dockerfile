# ---- Builder stage ----
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml .
COPY backend/src/ src/

RUN pip install --no-cache-dir --prefix=/install \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    .

# ---- Runtime stage ----
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libportaudio2 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY backend/alembic.ini .
COPY backend/alembic/ alembic/
COPY backend/src/ src/
COPY shared/ /app/shared/

ENV PYTHONPATH=/app/src:/app

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop"]
