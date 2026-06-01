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
RUN uv pip install --system --no-cache .[mcp]

WORKDIR /auditor
ENTRYPOINT ["auditor"]
CMD ["scan", "."]
