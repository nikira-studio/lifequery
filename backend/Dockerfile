FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /app/data is the single persistent volume — everything lives here
RUN mkdir -p /app/data/chroma /app/data/uploads /app/data/logs

ENV DATA_DIR=/app/data
ENV LOG_LEVEL=INFO

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
