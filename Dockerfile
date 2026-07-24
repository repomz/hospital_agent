# syntax=docker/dockerfile:1.7

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app hospital_agent.py ./
COPY --chown=app:app hospital_agent ./hospital_agent

RUN mkdir -p /data /config /work \
    && chown -R app:app /data /config /work

ARG VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="hospital-agent" \
      org.opencontainers.image.description="Hospital workstation agent for viewer_backend" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

USER app

VOLUME ["/data", "/work"]

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os; os.kill(1, 0)"]

ENTRYPOINT ["python", "-m", "hospital_agent"]
