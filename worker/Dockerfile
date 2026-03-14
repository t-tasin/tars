# ---- Builder stage ----
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    .

# ---- Runtime stage ----
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local

COPY src/ src/

ENV PYTHONPATH=/app/src

CMD ["python", "-m", "src.main"]
