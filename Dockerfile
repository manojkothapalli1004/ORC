# ORC — local-first operator console
# syntax=docker/dockerfile:1.6

FROM python:3.12-slim AS base

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv==0.10.11

WORKDIR /app

# Copy dependency manifests first so layer caching works on code-only changes
COPY pyproject.toml uv.lock README.md ./

# Install Python dependencies into an image-local venv
RUN uv sync --frozen --no-install-project

# Copy the rest of the project
COPY backend ./backend
COPY ui ./ui
COPY docs ./docs
COPY LICENSE NOTICE ./

# Install ORC as an editable package (registers the `orc` CLI)
RUN uv pip install -e .

# Persistent runtime state is mounted via volumes (see docker-compose.yml)
RUN mkdir -p /app/data /app/logs /app/bridge/builder_jobs

EXPOSE 8100

# Default: launch the HTTP server bound to all interfaces inside the container
ENTRYPOINT ["uv", "run", "orc"]
CMD ["start", "--host", "0.0.0.0", "--port", "8100"]
