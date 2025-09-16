import os, time, json, hashlib, random, requests, pika

# --- AMQP config (tomado por env cuando esté presente)
HOST  = os.getenv("RABBITMQ_HOST", "rabbitmq")
PORT  = int(os.getenv("RABBITMQ_PORT", "5672"))
USER  = os.getenv("RABBITMQ_USER", "guest")
PWD   = os.getenv("RABBITMQ_PASSWORD", "guest")
VHOST = os.getenv("RABBITMQ_VHOST", "/")
QUEUE = os.getenv("QUEUE_NAME", "pool.tasks")  # <- la cola que realmente vas a consumir

COORDINATOR_URL = os.getenv(
    "COORDINATOR_URL",
    "http://coordinador-integrador:5000/solved_task"
)

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
    h.update(data.encode("utf-8"))
    return h.hexdigest()

def sendResult(data):
    try:
        r = requests.post(COORDINATOR_URL, json=data, timeout=10)
        print("POST ->", COORDINATOR_URL, r.status_code, flush=True)
    except Exception as e:
        print("POST failed:", repr(e), flush=True)

def on_message_received(ch, method, properties, body):
    data = json.loads(body)
    print(f"Message {data} received", flush=True)

    encontrado = False
    intentos = 0
    startTime = time.time()

    print("## Iniciando Minero ##", flush=True)

    while not encontrado:
        intentos += 1
        randomNumber = str(random.randint(0, data['numMaxRandom']))
        hashCalculado = calculateHash(randomNumber + data['baseStringChain'] + data['blockchainContent'])
        if hashCalculado.startswith(data['prefijo']):
            encontrado = True
            processingTime = time.time() - startTime

            dataResult = {
                'blockId': data['blockId'],
                'processingTime': processingTime,
                'hash': hashCalculado,
                'result': randomNumber
            }

            print(f"[x] Prefijo {data['prefijo']} OK - HASH {hashCalculado}", flush=True)
            sendResult(dataResult)

    ch.basic_ack(delivery_tag=method.delivery_tag)
    print(f"ACK blockId {data['blockId']}", flush=True)

def main():
    while True:
        try:
            print("AMQP: connecting to", HOST, PORT, "vhost", VHOST, "queue", QUEUE, flush=True)
            connection = pika.BlockingConnection(pika_params())
            channel = connection.channel()

            # La cola en tu cluster es quorum:
            channel.queue_declare(queue=QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
            channel.basic_qos(prefetch_count=50)

            # Consumir DIRECTO de la cola (sin exchange/cola efímera)
            channel.basic_consume(queue=QUEUE, on_message_callback=on_message_received, auto_ack=False)
            print('AMQP: consuming… (CTRL+C para salir)', flush=True)
            channel.start_consuming()
        except KeyboardInterrupt:
            print("Consumption stopped by user.")
            try:
                connection.close()
            except Exception:
                pass
            break
        except Exception as e:
            print("AMQP error; retry in 5s:", repr(e), flush=True)
            time.sleep(5)

if __name__ == '__main__':
    main()
