# Screenshot2Meet service image: Python 3.13, CPU-only PaddleOCR, models baked in.
FROM python:3.13-slim

# Runtime libraries for PaddlePaddle (OpenMP) and OpenCV.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app/server
RUN pip install --no-cache-dir /app/server

# Download the OCR models and build the weekday table at build time,
# so the first request doesn't wait for a few hundred MB of downloads.
RUN screenshot2meet download-models

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn screenshot2meet.app:app --host 0.0.0.0 --port ${PORT}"]