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
ENV PYTHONUNBUFFERED=1 \
    RABBITMQ_HOST=35.231.169.162 \
    RABBITMQ_PORT=5672 \
    RABBITMQ_USER=admin \
    RABBITMQ_PASSWORD=admin1234! \
    RABBITMQ_VHOST=/ \
    RABBITMQ_QUEUE=pool.tasks \
    COORDINATOR_URL=http://35.231.169.162/solved_task

CMD ["python","-u","worker_cpu.py"]
