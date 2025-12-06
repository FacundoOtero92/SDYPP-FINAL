# ------------- Base -------------
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# utilidades mínimas (curl solo si querés HEALTHCHECK interno)
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ------------- Dependencias -------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


RUN useradd -m -u 10001 appuser
USER 10001

# copia del código
COPY coordinador_worker_alive.py /app/coordinador_worker_alive.py

# expone el puerto de Flask
EXPOSE 5003

# (opcional) healthcheck interno del contenedor
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD curl -fsS http://localhost:5003/status || exit 1

# comando de arranque: **python**, no gunicorn
CMD ["python", "-u", "coordinador_worker_alive.py"]
