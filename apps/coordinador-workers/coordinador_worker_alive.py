#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Coordinador de Workers (Heartbeats + Autoescalado propio sobre MIG)
-------------------------------------------------------------------
Responsabilidades:
1) Recibir heartbeats por HTTP de los workers (GPU/CPU) y persistirlos en Redis con TTL.
2) Censar workers vivos (GPU/CPU) a partir de las claves vivas en Redis.
3) Autoescalar MIG de CPU **directamente**:
   - Si HAY GPU vivos  -> MIG CPU = 0 (CPU solo fallback).
   - Si NO hay GPU:
       * backlog == 0                      -> MIG CPU = 0.
       * 0 < backlog < MIN_BACKLOG_FOR_CPU -> MIN_CPU_SI_HAY_BACKLOG (ej: 1).
       * backlog >= MIN_BACKLOG_FOR_CPU    -> cpus_deseadas = ceil(backlog / TAREAS_POR_CPU)
                                              acotado en [MIN_CPU_SI_HAY_BACKLOG, MAX_REPLICAS_CPU].
4) Exponer endpoints de observabilidad: /status y /workers.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse
import os
import time
import json
import uuid
import threading
from typing import List, Tuple

from flask import Flask, request, jsonify
import redis
import pika

#   Módulo para manejar MIG de CPU vía API de Compute Engine


import json as _json
import urllib.request as _urlreq
import urllib.error as _urlerr

GCP_PROJECT  = os.getenv("GCP_PROJECT", "").strip()
GCP_LOCATION = os.getenv("GCP_LOCATION", "").strip()   # ej: "us-central1-c"
MIG_NAME     = os.getenv("MIG_NAME", "").strip()
MIG_SCOPE    = os.getenv("MIG_SCOPE", "zone").strip().lower()  # "zone" o "region"


def _get_gcp_token() -> str:
    """Obtiene un access token del metadata server (dentro de GCE/GKE)."""
    req = _urlreq.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
    )
    req.add_header("Metadata-Flavor", "Google")
    with _urlreq.urlopen(req, timeout=5) as resp:
        data = _json.loads(resp.read().decode())
        return data["access_token"]


def _gcp_call(
    method: str,
    url_path: str,
    params: dict | None = None,
    body: dict | None = None,
) -> dict:
    """Llama a la API de Compute con Bearer token."""
    token = _get_gcp_token()
    base  = "https://compute.googleapis.com/compute/v1"
    url   = f"{base}{url_path}"

    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)

    data = None
    if body is not None:
        data = _json.dumps(body).encode("utf-8")

    req = _urlreq.Request(url, data=data, method=method.upper())
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with _urlreq.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return _json.loads(raw) if raw else {}
    except _urlerr.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        print(f"[MIG] HTTP {e.code} {url} -> {err_body}", flush=True)
        raise
    except Exception as e:
        print(f"[MIG] ERROR {_json.dumps({'url': url, 'err': str(e)})}", flush=True)
        raise


def _mgr_path() -> str:
    """Arma el path del MIG según sea zonal o regional."""
    if MIG_SCOPE == "region":
        if "-" not in GCP_LOCATION:
            region = GCP_LOCATION
        else:
            region = "-".join(GCP_LOCATION.split("-")[:2])
        return f"/projects/{GCP_PROJECT}/regions/{region}/instanceGroupManagers/{MIG_NAME}"
    else:
        # zonal
        return f"/projects/{GCP_PROJECT}/zones/{GCP_LOCATION}/instanceGroupManagers/{MIG_NAME}"


def obtener_instancias() -> int:
    """Lee el targetSize del MIG (cuántas instancias desea tener)."""
    mgr = _gcp_call("GET", _mgr_path())
    ts = int(mgr.get("targetSize", 0))
    print(f"[MIG] obtener_instancias -> targetSize={ts}", flush=True)
    return ts


def _resize_absolute(size: int):
    """Hace resize absoluto del MIG a 'size'."""
    path = _mgr_path() + "/resize"
    _gcp_call("POST", path, params={"size": int(size)})
    print(f"[MIG] resize -> {size}", flush=True)


def crear_instancias(n: int):
    """Ajusta el MIG a tamaño actual + n (no se usa en la lógica nueva, pero lo dejo)."""
    cur = obtener_instancias()
    new = max(cur + int(n), 0)
    _resize_absolute(new)
    print(f"[MIG] crear_instancias {n} -> nuevo targetSize={new}", flush=True)


def destruir_instancias():
    """Pone el MIG en 0 instancias."""
    _resize_absolute(0)
    print("[MIG] destruir_instancias -> targetSize=0", flush=True)


_has_mig = True  # habilita el loop de autoscaling si la config GCP está completa


#   APP Flask


app = Flask(__name__)


# Configuración (variables env)


# Redis (donde se guardan los heartbeats con TTL)
REDIS_HOST = os.getenv("REDIS_HOST", "redis-integrador")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB   = int(os.getenv("REDIS_DB", "0"))
HB_TTL_SEC = int(os.getenv("HB_TTL_SEC", "45"))

# RabbitMQ (para medir backlog en cola pool.tasks)
RABBIT_URL      = os.getenv("RABBITMQ_URL")
RABBIT_HOST     = os.getenv("RABBITMQ_HOST", "rabbitmq.servicios")
RABBIT_PORT_RAW = os.getenv("RABBITMQ_PORT", "5672")
RABBIT_USER     = os.getenv("RABBITMQ_USER", "admin")
RABBIT_PASS     = os.getenv("RABBITMQ_PASSWORD", "admin1234!")
POOL_QUEUE      = os.getenv("POOL_QUEUE", "pool.tasks")

# Autoescalado coordinador
ENABLE_AUTOSCALE    = os.getenv("ENABLE_AUTOSCALE", "false").lower() in ("1", "true", "yes")
CPU_BOOT_COUNT      = int(os.getenv("CPU_BOOT_COUNT", "2"))       # (ya casi no se usa)
CHECK_BACKLOG_SEC   = int(os.getenv("CHECK_BACKLOG_SEC", "10"))   # frecuencia de chequeo
MIN_BACKLOG_FOR_CPU = int(os.getenv("MIN_BACKLOG_FOR_CPU", "1"))  # backlog mínimo “serio”
ANTI_FLAP_SEC       = int(os.getenv("ANTI_FLAP_SEC", "20"))       # tiempo mínimo entre acciones

from math import ceil  # por si lo quisieras usar

# Capacidad por CPU y límites
TAREAS_POR_CPU         = int(os.getenv("TAREAS_POR_CPU", "5"))   # tareas que “aguanta” una CPU
MAX_REPLICAS_CPU       = int(os.getenv("MAX_REPLICAS_CPU", "10"))
MIN_CPU_SI_HAY_BACKLOG = int(os.getenv("MIN_CPU_SI_HAY_BACKLOG", "1"))

#   Redis


R = None  # conexión global (simple cache)


def _get_redis() -> redis.StrictRedis:
    """Devuelve una conexión viva a Redis, con reintentos y backoff exponencial."""
    global R
    if R is not None:
        return R

    delay = 1
    while True:
        try:
            r = redis.StrictRedis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True,
            )
            r.ping()
            print("[coord] Redis OK", flush=True)
            R = r
            return R
        except Exception as e:
            print(f"[coord] Redis no disponible: {e} (retry {delay}s)", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 15)



#   RabbitMQ: leer backlog de la cola del pool


def _pika_params():
    if RABBIT_URL:
        return pika.URLParameters(RABBIT_URL)

    host = RABBIT_HOST
    try:
        if "://" in RABBIT_PORT_RAW:
            u = urlparse(RABBIT_PORT_RAW)
            host = u.hostname or host
            port = u.port or 5672
        else:
            port = int(RABBIT_PORT_RAW)
    except Exception:
        port = 5672

    return pika.ConnectionParameters(
        host=host,
        port=port,
        credentials=pika.PlainCredentials(RABBIT_USER, RABBIT_PASS),
        heartbeat=30,
        blocked_connection_timeout=30,
        connection_attempts=20,
        retry_delay=3,
    )


def _get_backlog() -> int:
    """
    Lee el número de mensajes pendientes en la cola POOL_QUEUE (pool.tasks).
    Devuelve 0 si hay un error.
    """
    try:
        print(
            f"[autoscale] leyendo backlog de {POOL_QUEUE} en {RABBIT_HOST}:{RABBIT_PORT_RAW or '5672'}",
            flush=True,
        )
        conn = pika.BlockingConnection(_pika_params())
        ch   = conn.channel()
        q    = ch.queue_declare(queue=POOL_QUEUE, passive=True)
        cnt  = q.method.message_count
        print(f"[autoscale] backlog real segun Rabbit = {cnt}", flush=True)
        conn.close()
        return cnt
    except Exception as e:
        print("[coord] no pude leer backlog:", e, flush=True)
        return 0



#   Heartbeats en Redis


def _hb_key(worker_type: str, worker_id: str) -> str:
    """Formato de clave en Redis: heartbeat:worker:{type}:{id}."""
    return f"heartbeat:worker:{worker_type}:{worker_id}"


def _register_or_renew_hb(worker_type: str, worker_id: str):
    """Crea/renueva el heartbeat del worker en Redis con TTL HB_TTL_SEC."""
    payload = {"ts": time.time(), "id": worker_id, "type": worker_type}
    _get_redis().setex(_hb_key(worker_type, worker_id), HB_TTL_SEC, json.dumps(payload))


def _scan_ids(worker_type: str) -> List[str]:
    """Devuelve lista de IDs vivos para el tipo dado (gpu o cpu) usando SCAN en Redis."""
    ids = []
    for key in _get_redis().scan_iter(f"heartbeat:worker:{worker_type}:*"):
        try:
            ids.append(key.split(":")[-1])
        except Exception:
            pass
    return ids


def _count_workers() -> Tuple[List[str], List[str]]:
    """Retorna (lista_gpus, lista_cpus) vivas en este instante."""
    gpus = _scan_ids("gpu")
    cpus = _scan_ids("cpu")
    return gpus, cpus



#   Endpoints HTTP


@app.get("/status")
def status():
    """Salud del coordinador + censo de workers + (opcional) métricas de autoscaling."""
    try:
        gpus, cpus = _count_workers()
        resp = {
            "message": "running",
            "time": datetime.now(timezone.utc).isoformat(),
            "workers": {
                "gpu": {"count": len(gpus), "ids": gpus},
                "cpu": {"count": len(cpus), "ids": cpus},
            },
            "hb_ttl_sec": HB_TTL_SEC,
        }
        if ENABLE_AUTOSCALE:
            resp["autoscale"] = {
                "enabled": True,
                "backlog": _get_backlog(),
                "cpu_boot_count": CPU_BOOT_COUNT,
                "min_backlog_for_cpu": MIN_BACKLOG_FOR_CPU,
                "anti_flap_sec": ANTI_FLAP_SEC,
            }
        else:
            resp["autoscale"] = {"enabled": False}
        return jsonify(resp), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/workers")
def workers():
    """Lista detallada de workers vivos con su payload (timestamp/type/id)."""
    try:
        items = []
        r = _get_redis()
        for wtype in ("gpu", "cpu"):
            for wid in _scan_ids(wtype):
                payload_raw = r.get(_hb_key(wtype, wid))
                payload = json.loads(payload_raw) if payload_raw else None
                items.append({"id": wid, "type": wtype, "payload": payload})
        return jsonify({"workers": items}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/alive")
def alive():
    """
    Endpoint para que los workers reporten su heartbeat.

    JSON esperado: { "id": "<string| -1>", "type": "gpu|cpu" }

      - Si 'id' == -1 o vacío => se asigna un UUID y se devuelve al worker (registro inicial).
      - Si 'id' != -1         => se renueva el HB.
    """
    try:
        data  = request.get_json(force=True)
        wid   = str(data.get("id", "")).strip()
        wtype = str(data.get("type", "")).strip()

        if not wtype:
            return jsonify({"error": "type requerido (gpu|cpu)"}), 400

        # Registro inicial: asigno ID y creo la clave con TTL
        if wid in ("-1", "", None):
            wid = str(uuid.uuid4())
            _register_or_renew_hb(wtype, wid)
            return jsonify({"message": "worker registrado", "id": wid, "ttl": HB_TTL_SEC}), 200

        # Renovación de HB de un worker ya registrado
        _register_or_renew_hb(wtype, wid)
        return jsonify({"message": "hb ok", "id": wid, "ttl": HB_TTL_SEC}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500



#   Bucle de Autoescalado PROPIO sobre el MIG


def _autoscale_loop():
    """
    Lógica de autoscalado controlada por el coordinador:

    - Si HAY GPU vivos  -> MIG CPU en 0 (fallback apagado).
    - Si NO hay GPU:
        * backlog == 0                      -> MIG CPU en 0.
        * 0 < backlog < MIN_BACKLOG_FOR_CPU -> MIN_CPU_SI_HAY_BACKLOG (ej: 1).
        * backlog >= MIN_BACKLOG_FOR_CPU    -> cpus_deseadas = ceil(backlog / TAREAS_POR_CPU),
                                              acotado a [MIN_CPU_SI_HAY_BACKLOG, MAX_REPLICAS_CPU].
    """
    if not (ENABLE_AUTOSCALE and _has_mig):
        print("[coord] autoscaling deshabilitado (ENABLE_AUTOSCALE=false o _has_mig=False)", flush=True)
        return

    ultima_accion = 0.0
    idle_since = None
    while True:
        try:
            gpus_vivas, cpus_vivas = _count_workers()
            backlog = _get_backlog()
            ahora   = time.time()

            try:
                tamano_actual = obtener_instancias()
            except Exception as e:
                print(f"[autoscale] error al obtener tamaño del MIG: {e}", flush=True)
                time.sleep(CHECK_BACKLOG_SEC)
                continue

            print(
                f"[autoscale] gpus={len(gpus_vivas)} cpus={len(cpus_vivas)} "
                f"backlog={backlog} mig_size={tamano_actual}",
                flush=True,
            )

            # No hacer cambios muy seguidos
            if ahora - ultima_accion < ANTI_FLAP_SEC:
                time.sleep(CHECK_BACKLOG_SEC)
                continue

            # 1) Si HAY GPU -> bajar CPU a 0 (CPU es puro fallback)
            if len(gpus_vivas) > 0:
                if tamano_actual > 0:
                    print("[autoscale] hay GPU vivos -> forzando MIG CPU=0", flush=True)
                    destruir_instancias()
                    ultima_accion = ahora
                else:
                    print("[autoscale] hay GPU y MIG ya está en 0 -> sin cambios", flush=True)

            # 2) Si NO hay GPU -> escalar CPU según backlog
            else:
                if backlog <= 0:
                    # Sin trabajo: apagamos todo
                    cpus_deseadas = 0
                elif backlog < MIN_BACKLOG_FOR_CPU:
                    # Backlog chico: al menos 1 CPU si hay algo pendiente
                    cpus_deseadas = MIN_CPU_SI_HAY_BACKLOG
                else:
                    # Backlog “serio”: escalar proporcional
                    cpus_deseadas = (backlog + TAREAS_POR_CPU - 1) // TAREAS_POR_CPU

                    if cpus_deseadas < MIN_CPU_SI_HAY_BACKLOG:
                        cpus_deseadas = MIN_CPU_SI_HAY_BACKLOG
                    if cpus_deseadas > MAX_REPLICAS_CPU:
                        cpus_deseadas = MAX_REPLICAS_CPU

                print(f"[autoscale] sin GPU -> cpus_deseadas={cpus_deseadas}", flush=True)

                if cpus_deseadas != tamano_actual:
                    print(f"[autoscale] resize MIG de {tamano_actual} a {cpus_deseadas}", flush=True)
                    _resize_absolute(cpus_deseadas)
                    ultima_accion = ahora
                else:
                    print("[autoscale] tamaño de MIG ya coincide con lo deseado -> sin cambios", flush=True)

        except Exception as e:
            print("[autoscale] error general en loop:", e, flush=True)

        time.sleep(CHECK_BACKLOG_SEC)


def start_background_workers():
    """Arranca el hilo de autoescalado (si corresponde)."""
    t = threading.Thread(target=_autoscale_loop, daemon=True)
    t.start()



#   Main


if __name__ == "__main__":
    start_background_workers()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5003")))
