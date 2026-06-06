# syntax=docker/dockerfile:1
FROM python:3.13-slim

# git is needed for accurate .gitignore-aware discovery (git ls-files)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY auditor ./auditor
RUN uv pip install --system --no-cache ".[mcp,ts]"

# The shared index lives here (NOT inside the mounted repo at /auditor). Mount a named volume at
# this path to persist the incremental cache across runs: -v auditor-index:/root/.auditor
ENV AUDITOR_HOME=/root/.auditor

WORKDIR /auditor
# Default: the CLI (`docker run … scan .`). For the MCP server, override the entrypoint:
#   docker run -i --rm -v "$PWD:/auditor" --entrypoint auditor-mcp auditor:latest
ENTRYPOINT ["auditor"]
CMD ["scan", "."]
