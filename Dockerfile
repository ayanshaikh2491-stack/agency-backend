# syntax=docker/dockerfile:1.7
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl nodejs npm && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY admin ./admin
COPY app.py .
EXPOSE 7860
CMD ["python", "app.py"]
