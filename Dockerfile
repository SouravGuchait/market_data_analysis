# syntax=docker/dockerfile:1

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        curl \
        default-libmysqlclient-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system app \
    && useradd --system --uid 1000 --gid app --create-home app \
    && mkdir -p /var/log/mda /data/ingestion /var/lib/celery \
    && chown -R app:app /var/log/mda /data/ingestion /var/lib/celery

COPY --chown=app:app . .

USER app

EXPOSE 8000

# HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
#     CMD curl --fail --silent http://127.0.0.1:8000/api/v1/health/ || exit 1

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]
