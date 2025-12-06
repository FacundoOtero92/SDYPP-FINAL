
import os, time, json, hashlib, random, requests, pika, socket, threading
from prometheus_client import Counter, Histogram, push_to_gateway, REGISTRY


# CONFIG PUSHGATEWAY

PUSHGATEWAY_HOST = os.getenv("PUSHGATEWAY_HOST")
PUSHGATEWAY_PORT = int(os.getenv("PUSHGATEWAY_PORT", "9091"))

# METRICAS
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

# 2) Hashes probados (para hashes/segundo por nodo)
HASHES_PROBADOS_TOTAL = Counter(
    "hashes_probados_total",
    "Cantidad de hashes probados por este worker",
    ["tipo_worker"]
)

# 3) Tiempo de minado por dificultad/prefijo
TIEMPO_MINADO_POR_PREFIJO_SEGUNDOS = Histogram(
    "tiempo_minado_por_prefijo_segundos",
    "Tiempo de minado por bloque discriminado por prefijo (dificultad)",
    ["tipo_worker", "prefijo"]
)

# 4) Latencia cola RabbitMQ → worker
LATENCIA_COLA_SEGUNDOS = Histogram(
    "latencia_cola_segundos",
    "Tiempo desde que el coordinador publica la tarea hasta que el worker la empieza a procesar",
    ["tipo_worker"]
)

# --- AMQP config (igual que tenías)
HOST  = os.getenv("RABBITMQ_HOST", "rabbitmq.servicios")
PORT  = int(os.getenv("RABBITMQ_PORT", "5672"))
USER  = os.getenv("RABBITMQ_USER", "guest")
PWD   = os.getenv("RABBITMQ_PASSWORD", "guest")
VHOST = os.getenv("RABBITMQ_VHOST", "/")
QUEUE = os.getenv("RABBITMQ_QUEUE") or os.getenv("QUEUE_NAME", "pool.tasks")

COORDINATOR_URL = os.getenv("COORDINATOR_URL","http://35.231.169.162/solved_task")

# --- NUEVO: config de heartbeat HTTP hacia el coordinador de workers (puerto 5003)
WORKER_TYPE     = os.getenv("WORKER_TYPE","cpu")          # "cpu" o "gpu"
WORKER_ID       = os.getenv("WORKER_ID") or socket.gethostname()
HEARTBEAT_PERIOD= int(os.getenv("HEARTBEAT_PERIOD","7"))  # segundos entre heartbeats
HEARTBEAT_URL   = os.getenv("HEARTBEAT_URL","http://35.231.169.162/workers-hb/alive")
def pika_params():
    return pika.ConnectionParameters(
        host=HOST, port=PORT, virtual_host=VHOST,
        credentials=pika.PlainCredentials(USER, PWD),
        heartbeat=30, blocked_connection_timeout=300,
        connection_attempts=50, retry_delay=5, socket_timeout=10,
        client_properties={"connection_name": os.getenv("WORKER_ID","worker")}
    )

def calculateHash(data):
    h = hashlib.md5()
    h.update(data.encode('utf-8'))
    return h.hexdigest()

def sendResult(data):
    try:
        r = requests.post(COORDINATOR_URL, json=data, timeout=10)
        print("POST ->", COORDINATOR_URL, r.status_code, flush=True)
    except Exception as e:
        print("POST failed:", repr(e), flush=True)

# --------- NUEVO: registro + loop de heartbeat ---------
_assigned_id = None  # se setea al registrarse

def _register_with_coordinator():
    """
    Si no tenemos ID (o enviamos -1), pedimos uno al coordinador de workers.
    Devuelve el ID asignado (string).
    """
    global _assigned_id
    payload = {"id": -1, "type": WORKER_TYPE}
    try:
        resp = requests.post(HEARTBEAT_URL, json=payload, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            _assigned_id = data.get("id") or WORKER_ID  # fallback al hostname por si acaso
            print(f"[HB] registrado con id={_assigned_id}", flush=True)
        else:
            print(f"[HB] registro falló: {resp.status_code} {resp.text}", flush=True)
    except Exception as e:
        print("[HB] error de registro:", repr(e), flush=True)

def _heartbeat_loop():
    """
    Envía keep-alive periódico al coordinador de workers.
    Si aún no tiene id asignado, intenta registrarse.
    """
    global _assigned_id
    while True:
        try:
            if not _assigned_id:
                _register_with_coordinator()
            else:
                payload = {"id": _assigned_id, "type": WORKER_TYPE}
                resp = requests.post(HEARTBEAT_URL, json=payload, timeout=5)
                print(f"[HB] POST {HEARTBEAT_URL} => {resp.status_code}", flush=True)
        except Exception as e:
            print("[HB] error:", repr(e), flush=True)
        time.sleep(HEARTBEAT_PERIOD)

# -------------------------------------------------------

def on_message_received(ch, method, properties, body):
    # momento en que el worker recibe el mensaje
    recibido_en = time.time()

    data = json.loads(body)
    print(f"Message {data} received", flush=True)

    # ====== LATENCIA COLA REAL USANDO "created" ======
    created = data.get("created")
    if created is not None:
        try:
            lat = recibido_en - float(created)
            if lat < 0:
                lat = 0  # por si hay relojes medio corridos
            LATENCIA_COLA_SEGUNDOS.labels(WORKER_TYPE).observe(lat)
            print(f"[METRICAS] Latencia cola = {lat:.3f} s", flush=True)
        except Exception as e:
            print("[METRICAS] error calculando latencia:", repr(e), flush=True)
    # ================================================

    # Normalizar esquema (soporta 'block' y plano)
    if 'block' in data:
        blk = data['block']
        block_id = blk['blockId']
        base    = str(blk.get('baseStringChain', ''))
        content = str(blk.get('blockchainContent') or '')
        prefijo = str(blk.get('prefijo', ''))
        num_max = blk.get('numMaxRandom')
        r = data.get('range', {})
        num_min = r.get('min', 0)
        if num_max is None:
            num_max = r.get('max', 99999999)
    else:
        blk = data
        block_id = data['blockId']
        base = data['baseStringChain']
        content = data['blockchainContent']
        prefijo = data['prefijo']
        num_min = 0
        num_max = data.get('numMaxRandom', 99999999)

    encontrado = False
    startTime = time.time()
    print("## Iniciando Minero ##", flush=True)

    try:
        while not encontrado:
            randomNumber = str(random.randint(int(num_min), int(num_max)))
            # Cada intento de hash cuenta
            HASHES_PROBADOS_TOTAL.labels(WORKER_TYPE).inc()

            hashCalculado = calculateHash(randomNumber + base + content)
            if hashCalculado.startswith(prefijo):
                encontrado = True
                processingTime = time.time() - startTime

                # actualizo métricas 
                BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc()
                BLOQUES_MINADOS_EXITOSOS.labels(WORKER_TYPE).inc()
                TIEMPO_MINADO_SEGUNDOS.labels(WORKER_TYPE).observe(processingTime)
                # TIEMPO_MINADO_POR_PREFIJO_SEGUNDOS.labels(WORKER_TYPE, prefijo).observe(processingTime)

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
                # ==================================================
                dataResult = {
                    'blockId': block_id,
                    'processingTime': processingTime,
                    'hash': hashCalculado,
                    'result': randomNumber,
                    "workerType": WORKER_TYPE, 
                    "workerId": WORKER_ID      
                }
                print(f"[x] Prefijo {prefijo} OK - HASH {hashCalculado}", flush=True)
                sendResult(dataResult)

        ch.basic_ack(delivery_tag=method.delivery_tag)
        print(f"ACK blockId {block_id}", flush=True)
    except Exception as e:
        print("Miner error:", repr(e), flush=True)
        BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc()
        BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc()
        raise


def main():

    print("[METRICAS] Usando Pushgateway en:", PUSHGATEWAY_HOST, flush=True)
  
    # Inicializo métricas en 0 para que existan aunque no haya errores
    BLOQUES_MINADOS_EXITOSOS.labels(WORKER_TYPE).inc(0)
    BLOQUES_MINADOS_ERRONEOS.labels(WORKER_TYPE).inc(0)
    HASHES_PROBADOS_TOTAL.labels(WORKER_TYPE).inc(0)
    BLOQUES_MINADOS_TOTALES.labels(WORKER_TYPE).inc(0)


    #  arranco el hilo de heartbeat antes de consumir ----
    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    while True:
        try:
            print("AMQP: connecting to", HOST, PORT, "vhost", VHOST, "queue", QUEUE, flush=True)
            connection = pika.BlockingConnection(pika_params())
            channel = connection.channel()
            channel.exchange_declare(exchange="coordinator.inbox", exchange_type="direct", durable=True)
            channel.queue_declare(queue=QUEUE, durable=True)
            channel.basic_qos(prefetch_count=int(os.getenv("PREFETCH_COUNT","10")))
            channel.basic_consume(queue=QUEUE, on_message_callback=on_message_received, auto_ack=False)
            print('AMQP: consuming… (CTRL+C para salir)', flush=True)
            channel.start_consuming()
        except KeyboardInterrupt:
            print("Consumption stopped by user.")
            try: connection.close()
            except Exception: pass
            break
        except Exception as e:
            print("AMQP error; retry in 5s:", repr(e), flush=True)
            time.sleep(5)

if __name__ == '__main__':
    main()
