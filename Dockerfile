# ── Build stage: install Python dependencies ───────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System packages needed to compile any C extensions (psycopg2-binary is a
# pre-built wheel so no libpq-dev is required, but keep gcc for edge cases).
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for safety
RUN useradd --create-home --shell /bin/bash artarb

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=artarb:artarb . .

# Make entrypoint executable
RUN chmod +x docker-entrypoint.sh

# Playwright browsers are NOT pre-installed (large download, ~300 MB).
# If you enable browser-based scraping, run inside the container:
#   docker compose exec app playwright install chromium --with-deps

USER artarb

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "main.py"]
