#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, time, socket, threading, random, hashlib
import requests
import pika


from prometheus_client import Counter, Histogram, push_to_gateway, REGISTRY

# CONFIG RABBITMQ 
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
QUEUE_NAME = os.getenv("RABBITMQ_QUEUE", os.getenv("QUEUE_NAME"))

#  COORDINADORES 
# coordinador que recibe el resultado de minado 
RESULT_COORD_URL = os.getenv("COORDINATOR_URL", "http://35.231.169.162/solved_task")

# coordinador de workers (el Flask puerto 5003 expuesto en /workers-hb/* )
HB_URL = os.getenv("HEARTBEAT_URL", "http://35.231.169.162/workers-hb/alive")
WORKER_TYPE = os.getenv("WORKER_TYPE", "gpu")
WORKER_ID = os.getenv("WORKER_ID", socket.gethostname())
HEARTBEAT_PERIOD = int(os.getenv("HEARTBEAT_PERIOD", "7"))

PUSHGATEWAY_HOST = os.getenv("PUSHGATEWAY_HOST", "35.231.51.5")
PUSHGATEWAY_PORT = os.getenv("PUSHGATEWAY_PORT", "9091")
WORKER_ID = os.getenv("WORKER_ID", "gpu-externo")
WORKER_TYPE = "gpu"

# METRICAS PROMETHEUS 

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


# este es el archivo : minero_gpu.py
import minero_gpu  #  usa nvcc + md5.cu + json_output.txt



# Heartbeat

_assigned_id = None

def _register():
    global _assigned_id
    payload = {"id": -1, "type": WORKER_TYPE}
    try:
        r = requests.post(HB_URL, json=payload, timeout=5)
        if r.status_code == 200:
            data = r.json()
            _assigned_id = data.get("id") or WORKER_ID
            print(f"[HB] registrado como {_assigned_id}", flush=True)
        else:
            print(f"[HB] fallo registro {r.status_code} {r.text}", flush=True)
    except Exception as e:
        print("[HB] error registro:", e, flush=True)

def _hb_loop():
    global _assigned_id
    while True:
        try:
            if not _assigned_id:
                _register()
            else:
                r = requests.post(HB_URL, json={"id": _assigned_id, "type": WORKER_TYPE}, timeout=5)
                print(f"[HB] {r.status_code}", flush=True)
        except Exception as e:
            print("[HB] error:", e, flush=True)
        time.sleep(HEARTBEAT_PERIOD)


def pika_params():
    return pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        virtual_host=RABBITMQ_VHOST,
        credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD),
        heartbeat=30,
        blocked_connection_timeout=300,
        connection_attempts=40,
        retry_delay=5,
    )

def send_result(block_id, processing_time, hash_hex, nonce):
    payload = {
        "blockId": block_id,
        "processingTime": processing_time,
        "hash": hash_hex,
        "result": nonce,
        "workerType": WORKER_TYPE,
    }
    try:
        r = requests.post(RESULT_COORD_URL, json=payload, timeout=10)
        print(f"[RESULT] POST -> {RESULT_COORD_URL} = {r.status_code}", flush=True)
    except Exception as e:
        print("[RESULT] error:", e, flush=True)

# CPU   
def cpu_fallback(prefix: str, hash_val: str, inicio: int, fin: int):
    """
    Búsqueda bruta por CPU si la GPU falla.
    Acá base+content ya viene en hash_val, así que hasheo + hash_val.
    """
    print("[CPU-FALLBACK] buscando por CPU...", flush=True)
    while True:
        n = str(random.randint(inicio, fin))
        # contamos el intento de hash
        HASHES_PROBADOS_TOTAL.labels(WORKER_TYPE).inc()
        h = hashlib.md5((n + hash_val).encode("utf-8")).hexdigest()
        if h.startswith(prefix):
            return n, h


# Consume

def on_message(ch, method, properties, body):
    """
    Esperamos lo mismo que tu worker CPU:
    {
        "block": {
            "blockId": "...",
            "prefijo": "000",
            "baseStringChain": "A3F8",
            "blockchainContent": "",
            "numMaxRandom": 99999999
        },
        "range": {"min": 0, "max": 99999999}
    }
    o a veces sin "range".
    """
    recibido_en = time.time()
    start_total = recibido_en
    msg = json.loads(body)
    print("[MSG] recibido:", msg, flush=True)

     # LATENCIA COLA "created" 
    created = msg.get("created")
    if created is not None:
        try:
            lat = recibido_en - float(created)
            if lat < 0:
                lat = 0  
            LATENCIA_COLA_SEGUNDOS.labels(WORKER_TYPE).observe(lat)
            print(f"[METRICAS] Latencia cola = {lat:.3f} s", flush=True)
        except Exception as e:
            print("[METRICAS] error calculando latencia:", e, flush=True)


   
    if "block" in msg:
        blk = msg["block"]
        block_id = blk["blockId"]
        prefix   = str(blk.get("prefijo", "000"))
        base     = str(blk.get("baseStringChain", ""))
        content  = str(blk.get("blockchainContent") or "")
        num_max  = blk.get("numMaxRandom")
        r = msg.get("range", {})
        from_val = int(r.get("min", 0))
        if num_max is None:
            num_max = int(r.get("max", 99999999))
        to_val = int(num_max)
    else:
        
        block_id = msg["blockId"]
        prefix   = str(msg.get("prefijo", "000"))
        base     = str(msg.get("baseStringChain", ""))
        content  = str(msg.get("blockchainContent") or "")
        from_val = 0
        to_val   = int(msg.get("numMaxRandom", 99999999))

    hash_val = base + content

    # Latencia cola 
    LATENCIA_COLA_SEGUNDOS.labels(WORKER_TYPE).observe(0.0)

    print(f"[GPU] minando block={block_id} rango {from_val}-{to_val} prefijo={prefix} base='{hash_val}'", flush=True)

   
    gpu_ok = False
    try:
        contenido = minero_gpu.ejecutar_minero(from_val, to_val, prefix, hash_val) 
        
        data_json = json.loads(contenido)
        numero = str(data_json.get("numero", ""))
        hash_md5 = data_json.get("hash_md5_result", "")
        if hash_md5:
            proc_time = time.time() - start_total
            print(f"[GPU] ENCONTRADO nonce={numero} hash={hash_md5}", flush=True)

            #  MÉTRICAS 
            BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc()
            BLOQUES_MINADOS_EXITOSOS.labels(WORKER_TYPE).inc()
            TIEMPO_MINADO_SEGUNDOS.labels(WORKER_TYPE).observe(proc_time)
            TIEMPO_MINADO_POR_PREFIJO_SEGUNDOS.labels(WORKER_TYPE, prefix).observe(proc_time)

            
           
            HASHES_PROBADOS_TOTAL.labels(WORKER_TYPE).inc(0)

            try:
                push_to_gateway(
                    f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
                    job="worker_minero",
                    grouping_key={"instance": WORKER_ID, "tipo_worker": WORKER_TYPE},
                    registry=REGISTRY,
                )
                print(f"[METRICAS] push a {PUSHGATEWAY_HOST} OK", flush=True)
            except Exception as e:
                print("[METRICAS] error al pushear al pushgateway:", repr(e), flush=True)
           

            send_result(block_id, proc_time, hash_md5, numero)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            gpu_ok = True
        else:
            print("[GPU] minero terminó pero no devolvió hash -> resuelvo por CPU", flush=True)
    except Exception as e:
        print("[GPU] error ejecutando minero:", e, flush=True)

    #  Si la GPU no resolvió, usamos CPU FALLBACK
    if not gpu_ok:
        try:
            nonce, h = cpu_fallback(prefix, hash_val, from_val, to_val)
            proc_time = time.time() - start_total
            print(f"[Resolvi por cpu] ENCONTRADO nonce={nonce} hash={h}", flush=True)

            # Métricas para bloque exitoso (resuelto por CPU pero sigue siendo worker GPU)
            BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc()
            BLOQUES_MINADOS_EXITOSOS.labels(WORKER_TYPE).inc()
            TIEMPO_MINADO_SEGUNDOS.labels(WORKER_TYPE).observe(proc_time)
            TIEMPO_MINADO_POR_PREFIJO_SEGUNDOS.labels(WORKER_TYPE, prefix).observe(proc_time)

            try:
                push_to_gateway(
                    f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
                    job="worker_minero",
                    grouping_key={"instance": WORKER_ID, "tipo_worker": WORKER_TYPE},
                    registry=REGISTRY,
                )
                print(f"[METRICAS] push a {PUSHGATEWAY_HOST} OK (Resolvi por cpu)", flush=True)
            except Exception as e:
                print("[METRICAS] error al pushear al pushgateway (Resolvi por cpu):", repr(e), flush=True)

            send_result(block_id, proc_time, h, nonce)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        except Exception as e:
            print("[Resolvi por cpu] error:", e, flush=True)
            # Si GPU y CPU fallan, recién ahí marcamos error
            BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc()
            BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc()
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return

def main():
    print(f"[INIT] Worker GPU arrancando. WORKER_ID={WORKER_ID}, tipo={WORKER_TYPE}", flush=True)
    print(f"[INIT] Pushgateway: {PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}", flush=True)

    # Inicializo métricas en 0 para que existan aunque no haya errores
    BLOQUES_MINADOS_EXITOSOS.labels(WORKER_TYPE).inc(0)
    BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc(0)
    BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc(0)
    HASHES_PROBADOS_TOTAL.labels(WORKER_TYPE).inc(0)

    # heartbeat en background
    threading.Thread(target=_hb_loop, daemon=True).start()

    while True:
        try:
            print("[AMQP] conectando...", flush=True)
            conn = pika.BlockingConnection(pika_params())
            ch = conn.channel()
            ch.queue_declare(queue=QUEUE_NAME, durable=True)
            ch.basic_qos(prefetch_count=int(os.getenv("PREFETCH_COUNT","5")))
            ch.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message, auto_ack=False)
            print("[AMQP] consumiendo cola", QUEUE_NAME, flush=True)
            ch.start_consuming()
        except Exception as e:
            print("[AMQP] error, reintento en 5s:", e, flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
