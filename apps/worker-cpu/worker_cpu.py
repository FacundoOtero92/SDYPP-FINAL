# import os, time, json, hashlib, random, requests, pika

# # --- AMQP config (tomado por env cuando esté presente)
# HOST  = os.getenv("RABBITMQ_HOST", "rabbitmq")
# PORT  = int(os.getenv("RABBITMQ_PORT", "5672"))
# USER  = os.getenv("RABBITMQ_USER", "guest")
# PWD   = os.getenv("RABBITMQ_PASSWORD", "guest")
# VHOST = os.getenv("RABBITMQ_VHOST", "/")
# # QUEUE = os.getenv("QUEUE_NAME", "pool.tasks")  # <- la cola que realmente vas a consumir
# QUEUE = os.getenv("RABBITMQ_QUEUE") or os.getenv("QUEUE_NAME", "pool.tasks")

# COORDINATOR_URL = os.getenv("COORDINATOR_URL","http://34.148.169.104/solved_task")

# def pika_params():
#     return pika.ConnectionParameters(
#         host=HOST, port=PORT, virtual_host=VHOST,
#         credentials=pika.PlainCredentials(USER, PWD),
#         heartbeat=30, blocked_connection_timeout=300,
#         connection_attempts=50, retry_delay=5, socket_timeout=10,
#         client_properties={"connection_name": os.getenv("WORKER_ID","worker")}
#     )

# def calculateHash(data):
#     h = hashlib.md5()
#     h.update(data.encode("utf-8"))
#     return h.hexdigest()

# def sendResult(data):
#     try:
#         r = requests.post(COORDINATOR_URL, json=data, timeout=10)
#         print("POST ->", COORDINATOR_URL, r.status_code, flush=True)
#     except Exception as e:
#         print("POST failed:", repr(e), flush=True)

# def on_message_received(ch, method, properties, body):
#     data = json.loads(body)
#     print(f"Message {data} received", flush=True)

#     # --- Normalizar esquema (soporta 'block' y plano) ---
#     if 'block' in data:
#         blk = data['block']
#         block_id = blk['blockId']
#         # base = blk['baseStringChain']
#         # content = blk['blockchainContent']
#         # prefijo = blk['prefijo']

#         base    = str(blk.get('baseStringChain', ''))
#         content = str(blk.get('blockchainContent') or '')
#         prefijo = str(blk.get('prefijo', ''))
#         num_max = blk.get('numMaxRandom')
#         # rango opcional en el envelope
#         r = data.get('range', {})
#         num_min = r.get('min', 0)
#         if num_max is None:
#             num_max = r.get('max', 99999999)
#     else:
#         # esquema antiguo/plano
#         blk = data
#         block_id = data['blockId']
#         base = data['baseStringChain']
#         content = data['blockchainContent']
#         prefijo = data['prefijo']
#         num_min = 0
#         num_max = data.get('numMaxRandom', 99999999)

#     encontrado = False
#     intentos = 0
#     startTime = time.time()
#     print("## Iniciando Minero ##", flush=True)

#     try:
#         while not encontrado:
#             intentos += 1
#             randomNumber = str(random.randint(int(num_min), int(num_max)))
#             hashCalculado = calculateHash(randomNumber + base + content)
#             if hashCalculado.startswith(prefijo):
#                 encontrado = True
#                 processingTime = time.time() - startTime

#                 dataResult = {
#                     'blockId': block_id,
#                     'processingTime': processingTime,
#                     'hash': hashCalculado,
#                     'result': randomNumber
#                 }

#                 print(f"[x] Prefijo {prefijo} OK - HASH {hashCalculado}", flush=True)
#                 sendResult(dataResult)

#         ch.basic_ack(delivery_tag=method.delivery_tag)
#         print(f"ACK blockId {block_id}", flush=True)
#     except Exception as e:
#         # si fallara algo, no ACKeamos: requeue tras reconexión
#         print("Miner error:", repr(e), flush=True)
#         raise

# def main():
#     while True:
#         try:
#             # print("AMQP: connecting to", HOST, PORT, "vhost", VHOST, "queue", QUEUE, flush=True)
#             # connection = pika.BlockingConnection(pika_params())
#             # channel = connection.channel()
#             print("AMQP: connecting to", HOST, PORT, "vhost", VHOST, "queue", QUEUE, flush=True)
#             connection = pika.BlockingConnection(pika_params())
#             channel = connection.channel()
#             channel.exchange_declare(exchange="coordinator.inbox", exchange_type="direct", durable=True)
#             # La cola en tu cluster es quorum:
#             channel.queue_declare(queue=QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
#             channel.basic_qos(prefetch_count=50)

#             # Consumir DIRECTO de la cola (sin exchange/cola efímera)
#             channel.basic_consume(queue=QUEUE, on_message_callback=on_message_received, auto_ack=False)
#             print('AMQP: consuming… (CTRL+C para salir)', flush=True)
#             channel.start_consuming()
#         except KeyboardInterrupt:
#             print("Consumption stopped by user.")
#             try:
#                 connection.close()
#             except Exception:
#                 pass
#             break
#         except Exception as e:
#             print("AMQP error; retry in 5s:", repr(e), flush=True)
#             time.sleep(5)

# if __name__ == '__main__':
#     main()

import os, time, json, hashlib, random, requests, pika, socket, threading

# --- AMQP config (igual que tenías)
HOST  = os.getenv("RABBITMQ_HOST", "rabbitmq")
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
    data = json.loads(body)
    print(f"Message {data} received", flush=True)

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
            hashCalculado = calculateHash(randomNumber + base + content)
            if hashCalculado.startswith(prefijo):
                encontrado = True
                processingTime = time.time() - startTime
                dataResult = {
                    'blockId': block_id,
                    'processingTime': processingTime,
                    'hash': hashCalculado,
                    'result': randomNumber
                }
                print(f"[x] Prefijo {prefijo} OK - HASH {hashCalculado}", flush=True)
                sendResult(dataResult)

        ch.basic_ack(delivery_tag=method.delivery_tag)
        print(f"ACK blockId {block_id}", flush=True)
    except Exception as e:
        print("Miner error:", repr(e), flush=True)
        raise

def main():
    # ---- NUEVO: arrancar el hilo de heartbeat antes de consumir ----
    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    while True:
        try:
            print("AMQP: connecting to", HOST, PORT, "vhost", VHOST, "queue", QUEUE, flush=True)
            connection = pika.BlockingConnection(pika_params())
            channel = connection.channel()
            channel.exchange_declare(exchange="coordinator.inbox", exchange_type="direct", durable=True)
            channel.queue_declare(queue=QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
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
