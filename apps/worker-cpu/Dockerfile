# worker_cpu.dockerfile
FROM python:3.10-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker_cpu.py .

# Vars overridable en runtime
ENV RABBITMQ_HOST=rabbitmq
ENV COORDINATOR_URL=http://coordinador-integrador:5000/solved_task

CMD ["python", "worker_cpu.py"]
