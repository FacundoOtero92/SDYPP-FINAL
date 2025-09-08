# apps/coordinator/Dockerfile
FROM python:3.11-slim

# Logs flush + no .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# (Opcional) toolchain por si alguna dep compila wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# Instalar deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY . .

# Usuario no-root (mejor práctica)
RUN useradd -m appuser
USER appuser

EXPOSE 5000

# IMPORTANTE: 1 solo worker (tu app lanza un thread que consume cola;
# si levantaras >1 worker tendrías consumidores duplicados)
CMD ["gunicorn", "coordinator:app", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:5000", "--timeout", "0", "--log-level", "info"]
