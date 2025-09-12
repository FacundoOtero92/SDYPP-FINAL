import hashlib
import json
import random
import threading
from flask import Flask, jsonify, request
import pika
import redis
import time
from google.cloud import storage

app = Flask(__name__)
@app.get("/alive")
def alive():
    return "ok", 200
# ========================
# VARIABLES
# =========================
hostRedis = 'redis-integrador'
portRedis = 6379
hostRabbit = 'rabbitmq'

queueNameTx   = 'QueueTransactions'  # cola de transacciones entrantes
exchangeBlock = 'ExchangeBlock'      # se mantiene declarado por compatibilidad

#  NUEVO: Inbox del worker-pool (coordinator -> pool)
POOL_EXCHANGE = 'coordinator.inbox'  # exchange (direct)
POOL_QUEUE    = 'pool.tasks'         # queue exclusiva del pool
POOL_RK       = 'tasks'              # routing key para la inbox

timer = 15
datosBucket = []
bucketName = 'bucket_integrador_final092025'
credentialPath = 'credentials.json'


# =========================
# Conexión a Redis
# =========================
def redisConnect():
    global client
    try:
        print(f"[DEBUG] Intentando conectar a Redis en {hostRedis}:{portRedis}", flush=True)
        client = redis.Redis(host=hostRedis, port=portRedis, db=0, decode_responses=True)
        if client.ping():
            print("[✅] Conectado a Redis exitosamente!", flush=True)
        client.set("test_key", "Hola Redis!")
        value = client.get("test_key")
        print(f"[DEBUG] Valor de test_key en Redis: {value}", flush=True)
        return client
    except Exception as e:
        print(f"[❌] Error conectando a Redis: {e}", flush=True)


# =========================
# Conexión a RabbitMQ
# =========================
def queueConnect():
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host=hostRabbit,
        port=5672,
        credentials=pika.PlainCredentials("guest", "guest"),
    ))
    channel = connection.channel()

    # Cola de transacciones
    channel.queue_declare(queue=queueNameTx, durable=True, arguments={"x-queue-type": "quorum"})

    # (Se mantiene) Exchange de trabajo para compatibilidad
    channel.exchange_declare(exchange=exchangeBlock, exchange_type='topic', durable=True)

    # NUEVO: Inbox del pool (direct + binding 'tasks')
    channel.exchange_declare(exchange=POOL_EXCHANGE, exchange_type='direct', durable=True)
    channel.queue_declare(queue=POOL_QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
    channel.queue_bind(exchange=POOL_EXCHANGE, queue=POOL_QUEUE, routing_key=POOL_RK)

    print(f'[x] Conectado a RabbitMQ', flush=True)
    return connection, channel


#  =========================
# Bucket (GCS)
#  =========================
def bucketConnect(bucketName):
    print(f"[DEBUG] Conectando al bucket: {bucketName}", flush=True)
    # bucketClient = storage.Client.from_service_account_json(credentialPath)
    bucketClient = storage.Client()
    bucket = bucketClient.bucket(bucketName)
    return bucket


# =========================
# Encolar transacciones
# =========================
def encolar(transaction):
    jsonTransaction = json.dumps(transaction)
    channel.basic_publish(exchange='',
                          routing_key=queueNameTx,
                          body=jsonTransaction,
                          properties=pika.BasicProperties(delivery_mode=2))
    print(f'[x] Se encoló en {queueNameTx}: {transaction}', flush=True)


# =========================
# Validaciones / Hash
# =========================
def validarTransaction(transaction):
    try:
        return bool(transaction['origen'] and transaction['destino'] and transaction['monto'])
    except Exception:
        return False

def calculateHash(data):
    hash_md5 = hashlib.md5()
    hash_md5.update(data.encode('utf-8'))
    return hash_md5.hexdigest()


# =========================
# Métodos Redis
# =========================
def getUltimoBlock():
    ultimoBlock = client.lindex('blockchain', 0)
    print("entro en get ultimo block")
    print(f"ultimo block:  {ultimoBlock}")
    if ultimoBlock:
        return json.loads(ultimoBlock)
    return None

def existBlock(id):
    allBlocks = client.lrange('blockchain', 0, -1)
    for block in allBlocks:
        msg = json.loads(block)
        if 'blockId' in msg and msg['blockId'] == id:
            return True
    # ← BUG FIX: el return False estaba dentro del for
    return False

def postBlock(block):
    if client is None:
        print("[ERROR] No hay conexión a Redis. No se guardará el bloque.", flush=True)
        return
    blockJson = json.dumps(block)
    client.lpush('blockchain', blockJson)
    saved_block = client.lrange('blockchain', 0, -1)
    print(f"[DEBUG] Bloques en Redis después de guardar: {saved_block}", flush=True)


# =========================
# Bucket: subir/descargar block
# =========================
def subirBlock(bucket, block):
    blockId = block['blockId']
    jsonBlock = json.dumps(block)
    fileName = f'block_{blockId}.json'
    blob = bucket.blob(fileName)
    blob.upload_from_string(jsonBlock, content_type='application/json')
    print(f"[x] El bloque {blockId} fue subido al bucket como {fileName}", flush=True)

def descargarBlock(bucket, blockId):
    fileName = f'block_{blockId}.json'
    blob = bucket.blob(fileName)
    jsonBlock = blob.download_as_text()
    block = json.loads(jsonBlock)
    print(f"[x] {blockId} descargado del Bucket", flush=True)
    return block


# =========================
# Endpoints Flask
# =========================
@app.route('/transaction', methods=['POST'])
def addTransaction():
    transaction = request.json
    try:
        if validarTransaction(transaction):
            encolar(transaction)
        else:
            return 'Transacción inválida', 400
    except Exception as e:
        print("[x] La transacción no es válida", e, flush=True)
        return 'Transacción no recibida', 400
    return 'Transacción recibida', 200

@app.route('/status', methods=['GET'])
def status():
    return jsonify({'status': 'OK'})

@app.route('/solved_task', methods=['POST'])
def receive_solved_task():
    newBlock = {
        'blockId': None,
        'hash': None,
        'hashPrevio': None,
        'nonce': None,
        'prefijo': None,
        'transactions': None,
        'timestamp': None,
        'blockchainContent': None,
        'baseStringChain': None
    }
    data = request.get_json()
    global datosBucket

    if not data:
        return jsonify({'status': 'error', 'message': 'No data received'}), 400

    print(f"Received data: {data}", flush=True)
    bucket = bucketConnect(bucketName)
    block = descargarBlock(bucket, data['blockId'])

    dataHash = data['result'] + block['baseStringChain'] + block['blockchainContent']
    hashResult = calculateHash(dataHash)
    timestamp = time.time()
    print(f"[x] Hash recibido:  {data.get('hash')}", flush=True)
    print(f"[x] Hash calculado: {hashResult}", flush=True)

    if hashResult == data.get('hash'):
        print('[x] Hash OK » Data válida.', flush=True)

        if existBlock(block['blockId']):
            print('[x] Existe Bloque » DESCARTAR', flush=True)
            return jsonify({'message': 'El bloque ya fue resuelto » DESCARTADO'}), 200

        print('[x] No existe bloque » Proceder', flush=True)

        # Conectar al bloque previo si existe
        try:
            ultimoBloque = getUltimoBlock()
        except Exception:
            ultimoBloque = None

        if ultimoBloque is not None:
            print('[x] Hay bloque anterior » Conectar', flush=True)
            newBlock['hashPrevio'] = ultimoBloque['hash']
            print(f"[x] Hash del último bloque: {ultimoBloque['hash']}", flush=True)
        else:
            print('[x] Bloque génesis', flush=True)
            newBlock['hashPrevio'] = None

        # Armar bloque final
        newBlock['blockId']          = data['blockId']
        newBlock['hash']             = data['hash']
        newBlock['transactions']     = block['transactions']
        newBlock['prefijo']          = block['prefijo']
        newBlock['baseStringChain']  = block['baseStringChain']
        newBlock['timestamp']        = timestamp
        newBlock['nonce']            = data['result']

        print(f"[DEBUG] Guardando bloque validado en Redis", flush=True)
        postBlock(newBlock)
        print('[x] Bloque validado » Agregado a la blockchain', flush=True)
        return jsonify({'message': 'Bloque validado » Agregado a la blockchain'}), 201
    else:
        print('[x] Hash inválido » DESCARTADO', flush=True)
        return jsonify({'message': 'El Hash recibido es inválido » DESCARTADO'}), 200


# =========================
# Loop: armar bloque y ENVIAR TAREA AL POOL
# =========================
def processPackages():
    while True:
        contadorTransaction = 0
        print('[x] Buscando Transacciones', flush=True)
        print('---------------------------', flush=True)
        print('', flush=True)
        listaTransactions = []

        # Levantar hasta 20 transacciones por ciclo
        for _ in range(20):
            method_frame, header_frame, body = channel.basic_get(queue=queueNameTx)
            if method_frame:
                contadorTransaction += 1
                listaTransactions.append(json.loads(body))
                print(f"[x] Desencolé una transacción", flush=True)
                print(f'[x] Transacción: {body}', flush=True)
                print('', flush=True)
                channel.basic_ack(method_frame.delivery_tag)
            else:
                print('[x] No hay transacciones', flush=True)
                print(f'[x] Cantidad desencoladas: {contadorTransaction}', flush=True)
                print('', flush=True)
                break

        if listaTransactions:
            print('', flush=True)

            maxRandom = 99999999
            blockId = str(random.randint(0, maxRandom))

            block = {
                "blockId": blockId,
                "transactions": listaTransactions,
                "prefijo": '000',
                "baseStringChain": "A3F8",
                "blockchainContent": getUltimoBlock()['blockchainContent'] if getUltimoBlock() else "0",
                "numMaxRandom": maxRandom
            }

            print(f"blockchainContent: {block['blockchainContent']}", flush=True)

             # Guardar en bucket (persistimos el bloque “madre”)
            global datosBucket
            datosBucket.append(block)
            bucket = bucketConnect(bucketName)
            subirBlock(bucket, block)

            # NUEVO: Publicar tarea madre a la INBOX del POOL
            task = {
                "type": "mine_block",
                "taskId": blockId,
                "range": {"min": 0, "max": maxRandom},
                "block": block
            }
            channel.basic_publish(
                exchange=POOL_EXCHANGE,
                routing_key=POOL_RK,
                body=json.dumps(task),
                properties=pika.BasicProperties(delivery_mode=2)  # persistente
            )
            print(f'[x] Tarea {blockId} enviada a {POOL_QUEUE}', flush=True)
            print('', flush=True)

        time.sleep(timer)


# =========================
# Bootstrap
# =========================
connection, channel = queueConnect()
client = redisConnect()

status_thread = threading.Thread(target=processPackages)
status_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0')
