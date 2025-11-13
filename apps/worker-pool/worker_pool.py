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

app = Flask(__name__)

# ============================
# CONFIG (vars de entorno)
# ============================
# Rabbit del cluster (MISMA cola que los workers individuales)
RABBIT_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBIT_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBIT_USER = os.getenv("RABBITMQ_USER", "admin")
RABBIT_PASS = os.getenv("RABBITMQ_PASSWORD", "admin1234!")
RABBIT_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBIT_QUEUE = os.getenv("RABBITMQ_QUEUE", "pool.tasks")   # misma cola que consumen todos

# Coordinator de WORKERS (el que me pasaste: /alive, /workers, etc.)
WORKER_COORD_URL = os.getenv("WORKER_COORD_URL", "http://coordinador-worker:5003/alive")

# Coordinator de BLOCKCHAIN (el que hace la validación final del bloque)
BLOCKCHAIN_COORD_URL = os.getenv("BLOCKCHAIN_COORD_URL", "http://coordinator:5000/solved_task")

HEARTBEAT_PERIOD = int(os.getenv("HB_PERIOD", "7"))

# ============================
# ESTADO DEL POOL
# ============================
# lista de workers GPU externos conectados
# estructura: { worker_id: {"last_seen": datetime, "pending_tasks": [ ... ] } }
workers_externos: Dict[str, dict] = {}
workers_lock = threading.Lock()

# este pool quiere verse como UNA GPU ante el coordinador de workers
POOL_ID = str(uuid.uuid4())

connection = None
channel = None

# ============================
# HELPERS
# ============================
def _has_external_workers() -> bool:
    with workers_lock:
        # un worker está “vivo” si su last_seen es reciente
        now = datetime.now(timezone.utc)
        vivos = [
            wid
            for (wid, info) in workers_externos.items()
            if (now - info["last_seen"]).total_seconds() < 20
        ]
        return len(vivos) > 0

def _register_external_worker() -> str:
    new_id = str(uuid.uuid4())
    with workers_lock:
        workers_externos[new_id] = {
            "last_seen": datetime.now(timezone.utc),
            "pending_tasks": []
        }
    print(f"[pool] worker externo registrado {new_id}", flush=True)
    return new_id

def _touch_external_worker(worker_id: str):
    with workers_lock:
        if worker_id in workers_externos:
            workers_externos[worker_id]["last_seen"] = datetime.now(timezone.utc)

def _enqueue_task_for_worker(worker_id: str, tarea: dict):
    with workers_lock:
        if worker_id in workers_externos:
            workers_externos[worker_id]["pending_tasks"].append(tarea)

def _pop_task_for_worker(worker_id: str):
    with workers_lock:
        info = workers_externos.get(worker_id)
        if not info:
            return None
        if not info["pending_tasks"]:
            return None
        return info["pending_tasks"].pop(0)

# ============================
# HTTP: workers externos
# ============================
@app.post("/alive")
def alive_external():
    """
    VMs GPU externas llaman acá.
    Si mandan {"id": -1} => las registramos y devolvemos id.
    Si mandan {"id": "<uuid>"} => solo renovamos last_seen.
    """
    data = request.get_json() or {}
    wid = str(data.get("id", "")).strip()

    if wid in ("", "-1", "0", None):
        new_id = _register_external_worker()
        return jsonify({"id": new_id}), 200

    # renovar
    _touch_external_worker(wid)
    return jsonify({"status": "ok"}), 200

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

    task = _pop_task_for_worker(wid)
    if task is None:
        return ("", 204)
    return jsonify(task), 200

@app.post("/solved_task")
def solved_from_external():
    """
    Un worker externo resolvió la tarea.
    La reenviamos al coordinador de blockchain.
    Además marcamos que ya no hace falta seguir esperando a los otros
    (eso lo podríamos hacer con un flag por blockId si querés).
    """
    payload = request.get_json() or {}
    print(f"[pool] resultado recibido de externo: {payload}", flush=True)

    try:
        r = requests.post(BLOCKCHAIN_COORD_URL, json=payload, timeout=5)
        print(f"[pool] reenviado al coordinador blockchain: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[pool] error reenviando al coordinador: {e}", flush=True)

    return jsonify({"status": "forwarded"}), 200

# ============================
# HILO: heartbeat condicional
# ============================
def heartbeat_loop():
    """
    Este pool SOLO se declara como 'gpu' ante el coordinador de workers
    si realmente tiene workers externos conectados.
    """
    while True:
        if _has_external_workers():
            try:
                r = requests.post(WORKER_COORD_URL, json={"id": POOL_ID, "type": "gpu"}, timeout=3)
                print(f"[pool] HB → coord workers {r.status_code}", flush=True)
            except Exception as e:
                print(f"[pool] HB error: {e}", flush=True)
        # si no hay workers, no mandamos nada
        time.sleep(HEARTBEAT_PERIOD)

# ============================
# HILO: limpiar externos viejos
# ============================
def cleaner_loop():
    while True:
        now = datetime.now(timezone.utc)
        with workers_lock:
            to_delete = []
            for wid, info in workers_externos.items():
                if (now - info["last_seen"]).total_seconds() > 60:
                    to_delete.append(wid)
            for wid in to_delete:
                print(f"[pool] limpiando worker externo inactivo {wid}", flush=True)
                del workers_externos[wid]
        time.sleep(10)

# ============================
# Rabbit: consumo de la MISMA cola
# ============================
def on_message(ch, method, properties, body):
    """
    Esto se ve como UN worker más.
    Pero en lugar de minar, parte la tarea según los externos conectados.
    """
    try:
        task = json.loads(body)
    except Exception:
        print("[pool] mensaje no es JSON, ACK igual", flush=True)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    # copio lista de workers vivos
    with workers_lock:
        vivos: List[str] = list(workers_externos.keys())

    if not vivos:
        # no tengo a quién repartirle, me hago a un lado
        print("[pool] sin workers externos, ACK y salgo", flush=True)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    # asumimos que viene con task["range"] = {"min": ..., "max": ...}
    r = task.get("range", {})
    global_min = int(r.get("min", 0))
    global_max = int(r.get("max", 0))

    total = len(vivos)
    rango_total = global_max - global_min + 1
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

        _enqueue_task_for_worker(wid, tarea_worker)
        print(f"[pool]   -> {wid} rango {start}-{end}", flush=True)

        start = end + 1

    # ya la repartí, no quiero que otro worker del cluster la agarre
    ch.basic_ack(delivery_tag=method.delivery_tag)

def consume_loop():
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
        try:
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.queue_declare(queue=RABBIT_QUEUE, durable=True)
            channel.basic_qos(prefetch_count=5)
            channel.basic_consume(queue=RABBIT_QUEUE, on_message_callback=on_message)
            print(f"[pool] consumiendo de cola compartida '{RABBIT_QUEUE}'", flush=True)
            channel.start_consuming()
        except Exception as e:
            print(f"[pool] error consumiendo de rabbit: {e}, reintento en 3s", flush=True)
            time.sleep(3)

# ============================
# BOOTSTRAP
# ============================
if __name__ == "__main__":
    # hilo de HB condicional al coordinador de workers que nos pasaste
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    # hilo de limpieza de externos
    threading.Thread(target=cleaner_loop, daemon=True).start()
    # hilo que consume de la cola (igual que un worker normal)
    threading.Thread(target=consume_loop, daemon=True).start()

    # servidor HTTP para registrar externos y que pidan tareas
    app.run(host="0.0.0.0", port=5002)
