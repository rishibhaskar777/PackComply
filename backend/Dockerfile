FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UPLOAD_DIR=/app/uploads \
    PADDLE_PDX_CACHE_HOME=/app/.paddlex

# Runtime libraries for PaddlePaddle (OpenMP) and OpenCV (libGL, GLib).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
# Includes the migrations, the entrypoint and the PDF fonts (backend/app/assets/fonts).
COPY backend ./backend
RUN pip install --no-cache-dir . \
    && sed -i 's/\r$//' backend/docker-entrypoint.sh \
    && chmod +x backend/docker-entrypoint.sh \
    && mkdir -p /app/uploads /app/.paddlex

EXPOSE 8000
ENTRYPOINT ["/app/backend/docker-entrypoint.sh"]
