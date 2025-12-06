#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import socket
import threading
import random
import hashlib

import requests

import minero_gpu  


# CONFIG


POOL_URL = os.getenv("POOL_URL", "http://35.231.169.162/worker-pool")
HEARTBEAT_PERIOD = int(os.getenv("HEARTBEAT_PERIOD", "7"))

ENDPOINT_ALIVE       = POOL_URL.rstrip("/") + "/alive"
ENDPOINT_NEXT_TASK   = POOL_URL.rstrip("/") + "/next_task"
ENDPOINT_SOLVED_TASK = POOL_URL.rstrip("/") + "/solved_task"

# id que te asigna el pool (uuid)
_worker_id_asignado = None


# HB contra el POOL

def registrar_en_pool():
    """
    Primer registro contra el pool.
    Enviamos id=-1 y el pool nos devuelve un uuid.
    """
    global _worker_id_asignado
    payload = {"id": -1}
    try:
        r = requests.post(ENDPOINT_ALIVE, json=payload, timeout=5)
        if r.status_code == 200:
            data = r.json()
            _worker_id_asignado = data.get("id") or socket.gethostname()
            print(f"[POOL-HB] registrado con id={_worker_id_asignado}", flush=True)
        else:
            print(f"[POOL-HB] fallo registro {r.status_code} {r.text}", flush=True)
    except Exception as e:
        print("[POOL-HB] error registrando contra pool:", e, flush=True)

def bucle_heartbeat_pool():
    """
    Mantiene vivo el worker en el pool (actualiza last_seen).
    """
    global _worker_id_asignado
    while True:
        try:
            if not _worker_id_asignado:
                registrar_en_pool()
            else:
                r = requests.post(
                    ENDPOINT_ALIVE,
                    json={"id": _worker_id_asignado},
                    timeout=5
                )
                print(f"[POOL-HB] {r.status_code}", flush=True)
        except Exception as e:
            print("[POOL-HB] error en heartbeat:", e, flush=True)
        time.sleep(HEARTBEAT_PERIOD)


# Obtener tareas del pool

def pedir_siguiente_tarea():
    """
    Pide al pool la próxima tarea.
    Si no hay, el pool responde 204.
    """
    if not _worker_id_asignado:
        return None
    try:
        r = requests.post(
            ENDPOINT_NEXT_TASK,
            json={"id": _worker_id_asignado},
            timeout=10
        )
        if r.status_code == 204:
            return None
        if r.status_code != 200:
            print(f"[POOL] /next_task devolvió {r.status_code}: {r.text}", flush=True)
            return None
        return r.json()
    except Exception as e:
        print("[POOL] error pidiendo tarea:", e, flush=True)
        return None


# Envío de resultado al pool

def enviar_resultado_al_pool(block_id, processing_time, hash_hex, nonce):
    """
    Envía el resultado al pool.
    El pool lo reenvía al coordinador de blockchain.
    """
    payload = {
        "blockId": block_id,
        "processingTime": processing_time,
        "hash": hash_hex,
        "result": nonce,
    }
    try:
        r = requests.post(ENDPOINT_SOLVED_TASK, json=payload, timeout=10)
        print(f"[POOL-RESULT] POST -> {ENDPOINT_SOLVED_TASK} = {r.status_code}", flush=True)
    except Exception as e:
        print("[POOL-RESULT] error enviando resultado al pool:", e, flush=True)


# Fallback CPU opcional

def cpu_fallback(prefijo, base, inicio, fin):
    """
    Búsqueda bruta por CPU si la GPU falla.
    """
    print("[CPU-FALLBACK] buscando por CPU...", flush=True)
    while True:
        n = str(random.randint(inicio, fin))
        h = hashlib.md5((n + base).encode("utf-8")).hexdigest()
        if h.startswith(prefijo):
            return n, h


# Procesar tarea

def procesar_tarea(tarea):
    """
    Recibe el JSON de la tarea desde el pool y ejecuta minero_gpu.
    Estructuras soportadas:
      - { "block": {...}, "range": {"min": ..., "max": ...} }
      - o plano: { "blockId": ..., "prefijo": ..., "baseStringChain": ..., ... }
    """
    inicio_total = time.time()
    print("[POOL-WORKER] tarea recibida:", tarea, flush=True)

    # Normalización de la estructura
    if "block" in tarea:
        blk = tarea["block"]
        block_id = blk["blockId"]
        prefijo = str(blk.get("prefijo", "000"))
        base = str(blk.get("baseStringChain", ""))
        contenido_extra = str(blk.get("blockchainContent") or "")
        num_max = blk.get("numMaxRandom")
        rango = tarea.get("range", {})
        desde = int(rango.get("min", 0))
        if num_max is None:
            num_max = int(rango.get("max", 99999999))
        hasta = int(num_max)
    else:
        block_id = tarea["blockId"]
        prefijo = str(tarea.get("prefijo", "000"))
        base = str(tarea.get("baseStringChain", ""))
        contenido_extra = str(tarea.get("blockchainContent") or "")
        desde = int(tarea.get("min", 0))
        hasta = int(tarea.get("numMaxRandom", 99999999))

    valor_hash = base + contenido_extra

    print(
        f"[POOL-WORKER] minando block={block_id} rango {desde}-{hasta} "
        f"prefijo={prefijo} base='{valor_hash}'",
        flush=True
    )

    # 1) Intento con GPU
    try:
        contenido = minero_gpu.ejecutar_minero(desde, hasta, prefijo, valor_hash)
        data_json = json.loads(contenido)
        numero = str(data_json.get("numero", ""))
        hash_md5 = data_json.get("hash_md5_result", "")

        if hash_md5:
            tiempo_proc = time.time() - inicio_total
            print(f"[POOL-WORKER] GPU ENCONTRÓ nonce={numero} hash={hash_md5}", flush=True)
            enviar_resultado_al_pool(block_id, tiempo_proc, hash_md5, numero)
            return

        print("[POOL-WORKER] minero GPU no devolvió hash -> fallback CPU", flush=True)

    except Exception as e:
        print("[POOL-WORKER] error ejecutando minero GPU:", e, flush=True)

    # 2) Fallback CPU
    try:
        nonce, h = cpu_fallback(prefijo, valor_hash, desde, hasta)
        tiempo_proc = time.time() - inicio_total
        print(f"[POOL-WORKER] CPU-FALLBACK ENCONTRÓ nonce={nonce} hash={h}", flush=True)
        enviar_resultado_al_pool(block_id, tiempo_proc, h, nonce)
    except Exception as e:
        print("[POOL-WORKER] CPU-FALLBACK también falló:", e, flush=True)


# Bucle principal

def bucle_principal():
    # hilo de heartbeat hacia el pool
    threading.Thread(target=bucle_heartbeat_pool, daemon=True).start()

    # loop de tareas
    while True:
        tarea = pedir_siguiente_tarea()
        if tarea is None:
            time.sleep(2)
            continue
        procesar_tarea(tarea)

if __name__ == "__main__":
    print(f"[POOL-WORKER] iniciando worker externo. POOL_URL={POOL_URL}", flush=True)
    bucle_principal()
