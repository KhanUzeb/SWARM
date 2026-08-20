FROM oven/bun:1 AS web
WORKDIR /web
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY --from=web /web/dist ./frontend/dist

RUN useradd --create-home --shell /bin/false swarm \
    && mkdir -p /app/data /tmp/swarm-sandbox \
    && chown -R swarm:swarm /app /tmp/swarm-sandbox

USER swarm

ENV SWARM_SANDBOX_DIR=/tmp/swarm-sandbox
ENV SWARM_DB_PATH=/app/data/swarm.db

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
