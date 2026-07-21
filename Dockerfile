# Build the React UI once, then serve it from the FastAPI container.
FROM node:22-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

ENV DATA_DIR=/app/data \
    FRONTEND_DIST_PATH=/app/frontend/dist \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend-builder /build/dist ./frontend/dist

# Match the existing persistent-data ownership while avoiding a root process.
RUN useradd --create-home --uid 1001 --shell /usr/sbin/nologin lifequery \
    && mkdir -p /app/data/chroma /app/data/uploads /app/data/logs \
    && chown -R lifequery:lifequery /app

USER 1001:1001
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
