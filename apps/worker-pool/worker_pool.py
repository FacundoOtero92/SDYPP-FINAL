#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
import time
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List

from flask import Flask, request, jsonify
import pika
import requests

from prometheus_client import Counter, Histogram, push_to_gateway, REGISTRY

app = Flask(__name__)

# ============================
# CONFIG (vars de entorno)
# ============================
# Rabbit del cluster (MISMA cola que los workers individuales)
RABBIT_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq.servicios")
RABBIT_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBIT_USER = os.getenv("RABBITMQ_USER", "admin")
RABBIT_PASS = os.getenv("RABBITMQ_PASSWORD", "admin1234!")
RABBIT_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBIT_QUEUE = os.getenv("RABBITMQ_QUEUE", "pool.tasks")   # misma cola que consumen todos

# Coordinator de WORKERS (el que ya tenés: /alive, /workers, etc.)
WORKER_COORD_URL = os.getenv(
    "WORKER_COORD_URL",
    "http://coordinador-workers.apps.svc.cluster.local:5003/alive"
)

# Coordinator de BLOCKCHAIN (el que hace la validación final del bloque)
BLOCKCHAIN_COORD_URL = os.getenv(
    "BLOCKCHAIN_COORD_URL",
     "http://coordinador.apps.svc.cluster.local/solved_task"
)

HEARTBEAT_PERIOD = int(os.getenv("HB_PERIOD", "7"))


# Pushgateway (mismo que CPU/GPU)
PUSHGATEWAY_HOST = os.getenv("PUSHGATEWAY_HOST", "35.231.51.5")
PUSHGATEWAY_PORT = int(os.getenv("PUSHGATEWAY_PORT", "9091"))

# El pool se reporta como otro "worker" más
WORKER_TYPE = "pool"
POOL_ID = os.getenv("POOL_ID", str(uuid.uuid4()))

# ====== MISMAS MÉTRICAS QUE CPU/GPU ======
BLOQUES_MINADOS_EXITOSOS = Counter(
    "bloques_minados_exitosos_total",
    "Cantidad de bloques minados con éxito",
    ["tipo_worker"]
)

BLOQUES_MINADOS_TOTALES = Counter(
    "bloques_minados_totales",
    "Cantidad de bloques que este worker procesó (exitosos + erróneos)",
    ["tipo_worker"]
)

TIEMPO_MINADO_SEGUNDOS = Histogram(
    "tiempo_minado_segundos",
    "Tiempo de minado por bloque en segundos",
    ["tipo_worker"]
)

BLOQUES_MINADOS_ERRONEOS = Counter(
    "bloques_minados_erroneos_total",
    "Cantidad de bloques que no se pudieron procesar correctamente",
    ["tipo_worker"]
)

HASHES_PROBADOS_TOTAL = Counter(
    "hashes_probados_total",
    "Cantidad de hashes probados por este worker",
    ["tipo_worker"]
)

TIEMPO_MINADO_POR_PREFIJO_SEGUNDOS = Histogram(
    "tiempo_minado_por_prefijo_segundos",
    "Tiempo de minado por bloque discriminado por prefijo (dificultad)",
    ["tipo_worker", "prefijo"]
)

LATENCIA_COLA_SEGUNDOS = Histogram(
    "latencia_cola_segundos",
    "Tiempo desde que el coordinador publica la tarea hasta que el worker la empieza a procesar",
    ["tipo_worker"]
)

#  MÉTRICA  ESPECÍFICA DEL POOL 
POOL_TAREAS_REPARTIDAS = Counter(
    "pool_tareas_repartidas_total",
    "Cantidad de bloques madre que el pool alcanzó a repartir entre workers externos"
)


# 
# ESTADO DEL POOL
# 
# estructura de workers externos:
# {
#   worker_id: {
#       "last_seen": datetime,
#       "pending_tasks": [ { ..task.. }, ... ],
#       "tarea_en_progreso": {
#           "task": { ..task.. },
#           "asignada_en": datetime
#       } | None
#   }
# }
workers_externos: Dict[str, dict] = {}
workers_lock = threading.Lock()

# este pool quiere verse como UNA GPU ante el coordinador de workers
POOL_ID = str(uuid.uuid4())

connection = None
channel = None


def _hay_workers_externos() -> bool:
    """
    Devuelve True si hay al menos un worker externo 'vivo'.
    Se usa sólo para decidir si el pool manda HB como GPU.
    """
    now = datetime.now(timezone.utc)
    with workers_lock:
        vivos = [
            wid
            for (wid, info) in workers_externos.items()
            if (now - info["last_seen"]).total_seconds() < 20
        ]
    return len(vivos) > 0


def _registrar_worker_externo(worker_id: str | None = None) -> str:
    """
    Registra un nuevo worker externo.
    Si el pool se reinició y el worker ya tiene un ID previo,
    lo re-registro con ese mismo ID.
    """
    wid = worker_id or str(uuid.uuid4())
    with workers_lock:
        workers_externos[wid] = {
            "last_seen": datetime.now(timezone.utc),
            "pending_tasks": [],
            "tarea_en_progreso": None,
        }
    print(f"[pool] worker externo registrado {wid}", flush=True)
    return wid



def _tocar_worker_externo(worker_id: str):
    """Actualiza el last_seen de un worker externo ya registrado."""
    with workers_lock:
        if worker_id in workers_externos:
            workers_externos[worker_id]["last_seen"] = datetime.now(timezone.utc)


def _encolar_tarea_para_worker(worker_id: str, tarea: dict):
    """Encola una tarea en la lista de pendientes de un worker."""
    with workers_lock:
        if worker_id in workers_externos:
            workers_externos[worker_id]["pending_tasks"].append(tarea)


def _obtener_tarea_para_worker(worker_id: str):
    """Saca una tarea pendiente para un worker y la marca como en progreso."""
    with workers_lock:
        info = workers_externos.get(worker_id)
        if not info:
            return None
        if not info["pending_tasks"]:
            return None
        tarea = info["pending_tasks"].pop(0)
        info["tarea_en_progreso"] = {
            "task": tarea,
            "asignada_en": datetime.now(timezone.utc)
        }
        return tarea


def _marcar_tarea_resuelta(worker_id: str, task_payload: dict | None = None):
    """Limpia la tarea_en_progreso de un worker (opcionalmente chequeando que coincida)."""
    with workers_lock:
        info = workers_externos.get(worker_id)
        if not info:
            return
        if info.get("tarea_en_progreso") is None:
            return
        if task_payload is not None:
            block_id_payload = task_payload.get("blockId")
            block_id_worker = info["tarea_en_progreso"]["task"].get("blockId")
            if (
                block_id_payload is not None
                and block_id_worker is not None
                and block_id_payload != block_id_worker
            ):
                # no coincide, no la limpiamos
                return
        info["tarea_en_progreso"] = None


# workers externos

@app.post("/alive")
def alive():
    """
    Registro y mantenimiento de workers externos.

    - Si viene id = -1 → le damos uno nuevo
    - Si viene id, pero el pool no lo conoce → lo re-registramos (reinicio del pod)
    - Si viene id conocido → sólo renovamos last_seen
    """
    data = request.get_json() or {}
    wid = str(data.get("id", "")).strip()

    # primer registro
    if wid in ("", "-1", "0", None):
        new_id = _registrar_worker_externo()
        return jsonify({"id": new_id}), 200

    # worker ya tiene id → ver si el pool lo conoce
    with workers_lock:
        exists = wid in workers_externos

    if not exists:
        # El pool se reinició y perdió todos los workers
        _registrar_worker_externo(worker_id=wid)
        print(f"[pool] worker externo re-registrado {wid}", flush=True)
    else:
        # renovamos last_seen
        _tocar_worker_externo(wid)

    return jsonify({"status": "ok", "id": wid}), 200



@app.post("/next_task")
def next_task():
    """
    Los workers externos piden su próxima tarea.
    Si no hay, devolvemos 204.
    """
    data = request.get_json() or {}
    wid = data.get("id")
    if not wid:
        return jsonify({"error": "id requerido"}), 400

    tarea = _obtener_tarea_para_worker(wid)
    if tarea is None:
        return ("", 204)
    return jsonify(tarea), 200


@app.post("/solved_task")
def solved_task():
    """
    Un worker externo resolvió la tarea.
    La reenviamos al coordinador de blockchain.
    Además limpiamos su tarea_en_progreso si vino el worker_id.
    """
    payload = request.get_json() or {}
    print(f"[pool] resultado recibido de externo: {payload}", flush=True)

    worker_id = payload.get("worker_id")
    payload["workerType"] = "pool"
    if worker_id:
        _marcar_tarea_resuelta(worker_id, payload)

    ok = False
    try:
        r = requests.post(BLOCKCHAIN_COORD_URL, json=payload, timeout=5)
        print(f"[pool] reenviado al coordinador blockchain: {r.status_code}", flush=True)
        ok = 200 <= r.status_code < 300
    except Exception as e:
        print(f"[pool] error reenviando al coordinador: {e}", flush=True)
        ok = False

    # Podrías, si quisieras, mapear esto a "éxito/error" a nivel bloque del pool
    if ok:
        BLOQUES_MINADOS_EXITOSOS.labels(WORKER_TYPE).inc()
        BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc()
    else:
        BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc()
        BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc()

    # push de métricas del pool
    try:
        push_to_gateway(
            f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
            job="worker_minero",  # MISMO job que CPU/GPU
            grouping_key={"instance": POOL_ID, "tipo_worker": WORKER_TYPE},
            registry=REGISTRY,
        )
        print("[METRICAS] push worker_pool (solved_task) OK", flush=True)
    except Exception as e:
        print("[METRICAS] error al pushear worker_pool (solved_task):", repr(e), flush=True)

    return jsonify({"status": "forwarded"}), 200


# heartbeat 

def bucle_heartbeat():
    """
    Este pool SOLO se declara como 'gpu' ante el coordinador de workers
    si realmente tiene workers externos conectados.
    """
    while True:
        if _hay_workers_externos():
            try:
                r = requests.post(
                    WORKER_COORD_URL,
                    json={"id": POOL_ID, "type": "gpu"},
                    timeout=3,
                )
                print(f"[pool] HB → coord workers {r.status_code}", flush=True)
            except Exception as e:
                print(f"[pool] HB error: {e}", flush=True)
        # si no hay workers, no mandamos nada
        time.sleep(HEARTBEAT_PERIOD)


# HILO: limpiar externos viejos y reasignar tareas

def bucle_limpieza_workers():
    """
    Limpia workers externos inactivos y reasigna sus tareas pendientes/en progreso.
    """
    while True:
        now = datetime.now(timezone.utc)
        tareas_a_reasignar: List[dict] = []

        with workers_lock:
            # detecto workers caídos
            caidos = []
            for wid, info in workers_externos.items():
                if (now - info["last_seen"]).total_seconds() > 60:
                    caidos.append(wid)

            # extraigo sus tareas
            for wid in caidos:
                info = workers_externos.get(wid)
                if not info:
                    continue
                print(f"[pool] limpiando worker externo inactivo {wid}", flush=True)

                # todas las pendientes
                tareas_a_reasignar.extend(info.get("pending_tasks", []))

                # la tarea en progreso también la reencolamos
                tep = info.get("tarea_en_progreso")
                if tep and tep.get("task"):
                    tareas_a_reasignar.append(tep["task"])

                # elimino el worker
                del workers_externos[wid]

            # reasigno round-robin entre los vivos (si hay)
            vivos_ids = list(workers_externos.keys())
            if tareas_a_reasignar and vivos_ids:
                idx = 0
                for tarea in tareas_a_reasignar:
                    destino = vivos_ids[idx % len(vivos_ids)]
                    workers_externos[destino]["pending_tasks"].append(tarea)
                    print(f"[pool] reasignando tarea de caído a {destino}", flush=True)
                    idx += 1

        # Si no hay vivos y hay tareas_a_reasignar, se perderían.
       
        time.sleep(10)


# Rabbit
def manejar_mensaje(ch, method, properties, body):
    """
    Esto se ve como UN worker más.
    Pero en lugar de minar, parte la tarea según los externos conectados.
    """
    inicio_proc = time.time()
    BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc()  # el pool recibió un bloque madre

   
    if not _hay_workers_externos():
        print("[pool] me quedé sin workers externos -> NACK + requeue y detengo consumo", flush=True)
        BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc()

        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        dur = time.time() - inicio_proc
        TIEMPO_MINADO_SEGUNDOS.labels(WORKER_TYPE).observe(dur)

        try:
            push_to_gateway(
                f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
                job="worker_minero",
                grouping_key={"instance": POOL_ID, "tipo_worker": WORKER_TYPE},
                registry=REGISTRY,
            )
            print("[METRICAS] push worker_pool (sin workers) OK", flush=True)
        except Exception as e:
            print("[METRICAS] error al pushear worker_pool (sin workers):", repr(e), flush=True)

        try:
            ch.stop_consuming()
        except Exception:
            pass
        return


    try:
        task = json.loads(body)
    except Exception:
        print("[pool] mensaje no es JSON, ACK igual", flush=True)
        BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc()
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Latencia cola: ahora - created (solo si ya sabemos que hay externos)
    created_ts = task.get("created")
    if created_ts:
        try:
            lat = time.time() - float(created_ts)
            LATENCIA_COLA_SEGUNDOS.labels(WORKER_TYPE).observe(lat)
            print(f"[pool] latencia cola (pool) = {lat:.4f}s", flush=True)
        except Exception:
            pass

    # Obtener lista de workers vivos 
    now = datetime.now(timezone.utc)
    with workers_lock:
        vivos: List[str] = [
            wid
            for (wid, info) in workers_externos.items()
            if (now - info["last_seen"]).total_seconds() < 20
        ]

    if not vivos:
        print("[pool] sin workers externos (lista vacía), NACK + requeue", flush=True)
        BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc()

        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        dur = time.time() - inicio_proc
        TIEMPO_MINADO_SEGUNDOS.labels(WORKER_TYPE).observe(dur)

        try:
            push_to_gateway(
                f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
                job="worker_minero",
                grouping_key={"instance": POOL_ID, "tipo_worker": WORKER_TYPE},
                registry=REGISTRY,
            )
            print("[METRICAS] push worker_pool (sin vivos) OK", flush=True)
        except Exception as e:
            print("[METRICAS] error al pushear worker_pool (sin vivos):", repr(e), flush=True)

        time.sleep(1)
        return

    #  Lógica de partición de rango entre vivos 
    r = task.get("range") or {}
    bloque = task.get("block") or {}

    if "min" in r or "max" in r:
        global_min = int(r.get("min", 0))
        global_max = int(r.get("max", global_min))
    else:
        global_min = 0
        global_max = int(bloque.get("numMaxRandom", 0))

    total = len(vivos)
    rango_total = max(global_max - global_min + 1, 0)
    if rango_total <= 0:
        print(f"[pool] rango inválido {global_min}-{global_max}, ACK y salgo", flush=True)
        BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc()
        ch.basic_ack(delivery_tag=method.delivery_tag)

        dur = time.time() - inicio_proc
        TIEMPO_MINADO_SEGUNDOS.labels(WORKER_TYPE).observe(dur)

        try:
            push_to_gateway(
                f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
                job="worker_minero",
                grouping_key={"instance": POOL_ID, "tipo_worker": WORKER_TYPE},
                registry=REGISTRY,
            )
            print("[METRICAS] push worker_pool (rango inválido) OK", flush=True)
        except Exception as e:
            print("[METRICAS] error al pushear worker_pool (rango inválido):", repr(e), flush=True)
        return

    paso = rango_total // total
    sobrante = rango_total % total

    print(f"[pool] recibi tarea, repartiendo {global_min}-{global_max} entre {total} externos", flush=True)

    start = global_min
    for i, wid in enumerate(vivos):
        end = start + paso - 1
        if i == total - 1:
            end += sobrante

        tarea_worker = task.copy()
        tarea_worker["range"] = {"min": start, "max": end}

        _encolar_tarea_para_worker(wid, tarea_worker)
        print(f"[pool]   -> {wid} rango {start}-{end}", flush=True)

        start = end + 1

    # ya la repartí, no quiero que otro worker del cluster la agarre
    ch.basic_ack(delivery_tag=method.delivery_tag)

    # MÉTRICAS de "éxito" para el pool 
    BLOQUES_MINADOS_EXITOSOS.labels(WORKER_TYPE).inc()
    POOL_TAREAS_REPARTIDAS.inc()

    dur = time.time() - inicio_proc
    TIEMPO_MINADO_SEGUNDOS.labels(WORKER_TYPE).observe(dur)

    prefijo = str(bloque.get("prefijo", ""))
    if prefijo:
        # TIEMPO_MINADO_POR_PREFIJO_SEGUNDOS.labels(WORKER_TYPE, prefijo).observe(dur)

    #Pool no prueba hashes 
     HASHES_PROBADOS_TOTAL.labels(WORKER_TYPE).inc(0)

    try:
        push_to_gateway(
            f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
            job="worker_minero",
            grouping_key={"instance": POOL_ID, "tipo_worker": WORKER_TYPE},
            registry=REGISTRY,
        )
        print(f"[METRICAS] push worker_pool (reparto OK, dur={dur:.3f}s) OK", flush=True)
    except Exception as e:
        print("[METRICAS] error al pushear worker_pool (reparto OK):", repr(e), flush=True)



def bucle_consumo_cola():
    global connection, channel
    creds = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    params = pika.ConnectionParameters(
        host=RABBIT_HOST,
        port=RABBIT_PORT,
        virtual_host=RABBIT_VHOST,
        credentials=creds,
        heartbeat=30,
    )
    while True:
       
        if not _hay_workers_externos():
            print("[pool] no hay workers externos -> no consumo de la cola", flush=True)
            time.sleep(2)
            continue

        try:
            print("[pool] hay workers externos -> conectando a Rabbit", flush=True)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.queue_declare(queue=RABBIT_QUEUE, durable=True)
            channel.basic_qos(prefetch_count=5)
            channel.basic_consume(
                queue=RABBIT_QUEUE,
                on_message_callback=manejar_mensaje
            )
            print(f"[pool] consumiendo de cola compartida '{RABBIT_QUEUE}'", flush=True)
            channel.start_consuming()
        except Exception as e:
            print(f"[pool] error consumiendo de rabbit: {e}, reintento en 3s", flush=True)
            time.sleep(3)
        finally:
            # cierro conexión para que cualquier mensaje unacked vuelva a la cola
            try:
                if connection and not connection.is_closed:
                    connection.close()
            except Exception:
                pass



if __name__ == "__main__":


     # Inicializo series para tipo_worker="pool"
    BLOQUES_MINADOS_EXITOSOS.labels(WORKER_TYPE).inc(0)
    BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc(0)
    BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc(0)
    HASHES_PROBADOS_TOTAL.labels(WORKER_TYPE).inc(0)
    # hilo de HB condicional al coordinador de workers
    threading.Thread(target=bucle_heartbeat, daemon=True).start()
    # hilo de limpieza de externos + reasignación
    threading.Thread(target=bucle_limpieza_workers, daemon=True).start()
    # hilo que consume de la cola (igual que un worker normal)
    threading.Thread(target=bucle_consumo_cola, daemon=True).start()

    # servidor HTTP para registrar externos y que pidan tareas
    app.run(host="0.0.0.0", port=5002)
