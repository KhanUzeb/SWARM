FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

RUN useradd --create-home --shell /bin/false swarm \
    && mkdir -p /app/data /tmp/swarm-sandbox \
    && chown -R swarm:swarm /app /tmp/swarm-sandbox

USER swarm

ENV SWARM_SANDBOX_DIR=/tmp/swarm-sandbox
ENV SWARM_DB_PATH=/app/data/swarm.db

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
