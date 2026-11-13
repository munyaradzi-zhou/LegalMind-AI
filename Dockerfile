# syntax=docker/dockerfile:1
# =============================================================================
# LegalMind AI — Multi-stage Dockerfile
# =============================================================================
# Stage 1: Build dependencies
# Stage 2: Production runtime (slim)
# =============================================================================

# ─── Stage 1: Builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files (README.md and LICENSE are referenced by pyproject.toml)
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# Install Python dependencies into /app/venv: the same path as in the runtime stage,
# because console scripts (uvicorn) hard-code their interpreter path.
# Behind a TLS-intercepting proxy, pass its CA bundle: --secret id=ca_cert,src=<bundle.pem>
RUN --mount=type=secret,id=ca_cert,required=false \
    if [ -f /run/secrets/ca_cert ]; then export PIP_CERT=/run/secrets/ca_cert; fi && \
    python -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir --upgrade pip setuptools && \
    /app/venv/bin/pip install --no-cache-dir .

# Bake tiktoken's token tables into the image, so containers don't download them from
# openaipublic.blob.core.windows.net at start-up.
ENV TIKTOKEN_CACHE_DIR=/app/tiktoken-cache
RUN /app/venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# ─── Stage 2: Production Runtime ─────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: run as non-root user
RUN groupadd -r legalmind && useradd -r -g legalmind -d /app -s /sbin/nologin legalmind

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/venv /app/venv
COPY --from=builder /app/tiktoken-cache /app/tiktoken-cache

# Set environment
ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    TIKTOKEN_CACHE_DIR=/app/tiktoken-cache \
    OTEL_SERVICE_NAME=legalmind-api

# One uvicorn worker per container (uvicorn reads WEB_CONCURRENCY); scale out with replicas.
ENV WEB_CONCURRENCY=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

# Switch to non-root user
USER legalmind

# Start the application
CMD ["uvicorn", "legalmind.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
