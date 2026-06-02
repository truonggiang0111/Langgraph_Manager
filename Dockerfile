FROM docker:29-cli AS dockercli

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANGGRAPH_STATE_DIR=/data/state

WORKDIR /app

COPY --from=dockercli /usr/local/bin/docker /usr/bin/docker

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -e ".[dev]"

EXPOSE 8899

CMD ["uvicorn", "langgraph_manager.app:app", "--host", "0.0.0.0", "--port", "8899"]
