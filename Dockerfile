# syntax=docker/dockerfile:1.7

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOSPITAL_AGENT_CONFIG=/app/agent_config.json

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app hospital_agent.py ./
COPY --chown=app:app hospital_agent ./hospital_agent
COPY --chown=app:app agent_config.docker.json ./agent_config.json

RUN mkdir -p /data /config \
    && chown -R app:app /data /config

ARG VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="hospital-agent" \
      org.opencontainers.image.description="Hospital workstation agent for viewer_backend" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

USER app

VOLUME ["/data", "/config"]

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os; os.kill(1, 0)"]

ENTRYPOINT ["python", "-m", "hospital_agent"]
