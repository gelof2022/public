FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY VERSION ./VERSION
COPY backend/entrypoint.sh /usr/local/bin/app-entrypoint

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /appdata \
    && chown -R appuser:appuser /app /appdata \
    && chmod 755 /usr/local/bin/app-entrypoint

EXPOSE 8000
ENTRYPOINT ["app-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
