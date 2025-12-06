import hashlib
import json
import random
import threading
from flask import Flask, jsonify, request
import pika
import redis
import time
from google.cloud import storage
import os
from pika.exceptions import AMQPConnectionError, ChannelWrongStateError, StreamLostError

from prometheus_client import Histogram, Counter, Gauge, push_to_gateway, REGISTRY


#  CONFIG PROMETHEUS
PUSHGATEWAY_HOST = os.getenv("PUSHGATEWAY_HOST", "35.231.51.5")
PUSHGATEWAY_PORT = int(os.getenv("PUSHGATEWAY_PORT", "9091"))
COORDINATOR_ID   = os.getenv("COORDINATOR_ID", "coordinator-1")
PREFIJO_FORZADO = os.getenv("PREFIJO_FORZADO", "")  

#  MÉTRICAS DE VALIDACIÓN DE BLOQUE R 
TIEMPO_VALIDACION_BLOQUE_SEGUNDOS = Histogram(
    "tiempo_validacion_bloque_segundos",
    "Tiempo total de validación de un bloque en el coordinador (descarga GCS, cálculo hash, Redis, etc.)"
)

BLOQUES_VALIDACION_OK = Counter(
    "bloques_validados_ok_total",
    "Cantidad de bloques validados correctamente por el coordinador"
)

BLOQUES_VALIDACION_ERROR = Counter(
    "bloques_validados_error_total",
    "Cantidad de bloques que no pasaron la validación (hash inválido, ya existente, etc.)"
)

# Longitud actual del prefijo de dificultad (en cantidad de ceros)
LONGITUD_PREFIJO_ACTUAL = Gauge(
    "dificultad_prefijo_longitud",
    "Longitud (en cantidad de ceros) del prefijo actual de dificultad",
    ["instance"]
)
# Inicializamos en 0 para que la métrica exista desde el arranque
LONGITUD_PREFIJO_ACTUAL.labels(COORDINATOR_ID).set(0)

# Tiempo de minado del bloque medido por el worker (processingTime)
#  instancia del coordinador y por prefijo de dificultad
TIEMPO_MINADO_WORKER_SEGUNDOS = Histogram(
    "tiempo_minado_worker_segundos",
    "Tiempo que tarda un worker en resolver una tarea de minado",
    ["tipo_worker", "prefijo"]   
)
TIEMPO_MINADO_WORKER_SEGUNDOS.labels(COORDINATOR_ID, "desconocido").observe(0)

BLOQUES_VALIDACION_RESULTADO = Counter(
    "bloques_validados_total",
    "Bloques validados (OK/ERROR) por tipo de worker",
    ["tipo_worker", "resultado"]
)

app = Flask(__name__)
@app.get("/alive")
def alive():
    return "ok", 200    
# ========================
# VARIABLES
# =========================
hostRedis = 'redis-integrador.servicios'
portRedis = 6379
hostRabbit = 'rabbitmq.servicios'

queueNameTx   = 'QueueTransactions'  # cola de transacciones entrantes
exchangeBlock = 'ExchangeBlock'      # se mantiene declarado por compatibilidad

#  NUEVO: Inbox del worker-pool (coordinator -> pool)
POOL_EXCHANGE = 'coordinator.inbox'  # exchange (direct)
POOL_QUEUE    = 'pool.tasks'         # queue exclusiva del pool
POOL_RK       = 'tasks'              # routing key para la inbox

timer = 15
datosBucket = []
bucketName = 'bucket_final2025'
credentialPath = 'credentials.json'
PREFIJO_DEFECTO = os.getenv("PREFIJO_DEFECTO", "000")


# Dificultad dinámica

# Tiempo objetivo entre bloques (en segundos)
TIEMPO_OBJETIVO_BLOQUE = int(os.getenv("TIEMPO_OBJETIVO_BLOQUE", "30"))

# Longitud mínima y máxima del prefijo (cantidad de ceros)
LONGITUD_MIN_PREFIJO = int(os.getenv("LONGITUD_MIN_PREFIJO", "3"))
LONGITUD_MAX_PREFIJO = int(os.getenv("LONGITUD_MAX_PREFIJO", "5"))

# Cantidad de bloques recientes a considerar para calcular el promedio
CANTIDAD_BLOQUES_HISTORIAL = int(os.getenv("CANTIDAD_BLOQUES_HISTORIAL", "10"))


# Conexión a Redis

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



# Conexión a RabbitMQ
#RabbitMQ: lock y conexión/canal globales 
rabbit_lock = threading.Lock()
connection = None
channel = None
def queueConnect():
    host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    port = int( os.getenv("RABBITMQ_PORT") or 5672)
    vhost = os.getenv("RABBITMQ_VHOST") or "/"
    user  =  os.getenv("RABBITMQ_USER") or "admin"
    pwd   = os.getenv("RABBITMQ_PASS") or ""

    params = pika.ConnectionParameters(
        host=host, port=port, virtual_host=vhost,
        credentials=pika.PlainCredentials(user, pwd),
        heartbeat=30, blocked_connection_timeout=30,
        connection_attempts=10, retry_delay=3,
    )

    for i in range(10):  # reintentos de arranque
        try:
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            # Declaraciones (idempotentes)
            channel.queue_declare(queue=queueNameTx, durable=True)
            channel.exchange_declare(exchange=exchangeBlock, exchange_type='topic', durable=True)
            channel.exchange_declare(exchange=POOL_EXCHANGE, exchange_type='direct', durable=True)
            channel.queue_declare(queue=POOL_QUEUE, durable=True)
            channel.queue_bind(exchange=POOL_EXCHANGE, queue=POOL_QUEUE, routing_key=POOL_RK)
            print('[x] Conectado a RabbitMQ', flush=True)
            return connection, channel
        except (AMQPConnectionError, StreamLostError, ChannelWrongStateError) as e:
            print(f"[rabbit] intento {i+1} falló: {e}", flush=True)
            time.sleep(1 + i)

    raise RuntimeError("RabbitMQ no disponible después de reintentos")


def asegurar_rabbit_channel():
    """
    Se asegura de que exista una conexión/canal válido a RabbitMQ.
    Si están cerrados o en None, intenta reconectar.
    """
    global connection, channel
    if connection is None or connection.is_closed or channel is None or channel.is_closed:
        print("[rabbit] conexión/canal cerrados, reconectando...", flush=True)
        connection, channel = queueConnect()

# Bucket (GCS)

def bucketConnect(bucketName):
    print(f"[DEBUG] Conectando al bucket: {bucketName}", flush=True)
    # bucketClient = storage.Client.from_service_account_json(credentialPath)
    bucketClient = storage.Client()
    bucket = bucketClient.bucket(bucketName)
    return bucket



# Encolar transacciones

def encolar(transaction):
    global connection, channel
    jsonTransaction = json.dumps(transaction)

    for intento in range(2):  # probamos hasta 2 veces
        try:
            asegurar_rabbit_channel()
            with rabbit_lock:
                channel.basic_publish(
                    exchange='',
                    routing_key=queueNameTx,
                    body=jsonTransaction,
                    properties=pika.BasicProperties(delivery_mode=2)
                )
            print(f'[x] Se encoló en {queueNameTx}: {transaction}', flush=True)
            return
        except (AMQPConnectionError, StreamLostError, ChannelWrongStateError) as e:
            print(f"[rabbit] error al publicar transacción (intento {intento+1}): {e}", flush=True)
            # cerramos conexión (si sigue viva) y volvemos a intentar
            try:
                if connection and not connection.is_closed:
                    connection.close()
            except Exception:
                pass
            connection, channel = queueConnect()

    # si llegamos acá, fallaron los 2 intentos
    raise RuntimeError("No se pudo publicar la transacción en RabbitMQ")



# Validaciones / Hash

def validarTransaction(transaction):
    try:
        return bool(transaction['origen'] and transaction['destino'] and transaction['monto'])
    except Exception:
        return False

def calculateHash(data):
    hash_md5 = hashlib.md5()
    hash_md5.update(data.encode('utf-8'))
    return hash_md5.hexdigest()



# Métodos Redis

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




# Dificultad dinámica (prefijo)


def obtener_prefijo_actual(client_redis):
    """
    Obtiene el prefijo actual desde Redis.
    Orden de prioridad:
      1) Clave 'dificultad:prefijo_actual' en Redis.
      2) Prefijo del último bloque de la blockchain.
      3) PREFIJO_DEFECTO.
    """
    try:
        # 1) Prefijo guardado explícitamente
        valor = client_redis.get("dificultad:prefijo_actual")
        if valor:
            return valor

        # 2) Prefijo del último bloque
        ultimo_raw = client_redis.lindex("blockchain", 0)
        if ultimo_raw:
            try:
                ultimo = json.loads(ultimo_raw)
                pref = ultimo.get("prefijo")
                if isinstance(pref, str) and pref and set(pref) == {"0"}:
                    return pref
            except Exception:
                pass

    except Exception as e:
        print(f"[dificultad] error obteniendo prefijo actual: {e}", flush=True)

    # 3) Fallback: default
    return PREFIJO_DEFECTO


def ajustar_dificultad(client_redis):
    """
    Calcula un nuevo prefijo (string de ceros) a partir de los últimos N bloques.
    - Se basa en el tiempo promedio entre bloque y bloque.
    - Si el promedio es menor al objetivo => sube dificultad (más ceros).
    - Si el promedio es mayor al objetivo => baja dificultad (menos ceros).
    - Siempre dentro de [LONGITUD_MIN_PREFIJO, LONGITUD_MAX_PREFIJO].
    - Guarda el prefijo resultante en 'dificultad:prefijo_actual' en Redis.
    """

    #  MODO TEST: prefijo fijo desde env 
    # Si PREFIJO_FORZADO viene seteado ( "00", "000", "0000"),
   
    if PREFIJO_FORZADO:
        longitud_forzada = len(PREFIJO_FORZADO)
        LONGITUD_PREFIJO_ACTUAL.labels(COORDINATOR_ID).set(longitud_forzada)
        client_redis.set("dificultad:prefijo_actual", PREFIJO_FORZADO)
        print(
            f"[dificultad] MODO TEST: usando prefijo forzado '{PREFIJO_FORZADO}' "
            f"(len={longitud_forzada})",
            flush=True
        )
        return PREFIJO_FORZADO
    # ======================================

    try:
        
        bloques_raw = client_redis.lrange("blockchain", 0, CANTIDAD_BLOQUES_HISTORIAL - 1)
        if len(bloques_raw) < 2:
            prefijo_actual = obtener_prefijo_actual(client_redis)
            print(f"[dificultad] menos de 2 bloques en historial, uso prefijo actual '{prefijo_actual}'", flush=True)
            client_redis.set("dificultad:prefijo_actual", prefijo_actual)
            return prefijo_actual

        bloques = []
        for raw in bloques_raw:
            try:
                b = json.loads(raw)
                if "timestamp" in b:
                    bloques.append(b)
            except Exception:
                continue

        if len(bloques) < 2:
            prefijo_actual = obtener_prefijo_actual(client_redis)
            print(f"[dificultad] no hay timestamps suficientes, uso prefijo actual '{prefijo_actual}'", flush=True)
            client_redis.set("dificultad:prefijo_actual", prefijo_actual)
            return prefijo_actual

        # Ordenamos por timestamp ascendente (del más viejo al más nuevo)
        bloques_ordenados = sorted(bloques, key=lambda x: x["timestamp"])

        # Calculamos diferencias de tiempo entre bloques consecutivos
        diferencias = []
        for i in range(len(bloques_ordenados) - 1):
            t1 = bloques_ordenados[i]["timestamp"]
            t2 = bloques_ordenados[i + 1]["timestamp"]
            delta = t2 - t1
            if delta > 0:
                diferencias.append(delta)

        if not diferencias:
            prefijo_actual = obtener_prefijo_actual(client_redis)
            print(f"[dificultad] diferencias vacías, uso prefijo actual '{prefijo_actual}'", flush=True)
            client_redis.set("dificultad:prefijo_actual", prefijo_actual)
            return prefijo_actual

        promedio = sum(diferencias) / len(diferencias)
        print(
            f"[dificultad] tiempo promedio entre bloques = {promedio:.2f} s "
            f"(objetivo = {TIEMPO_OBJETIVO_BLOQUE}s)",
            flush=True
        )

        # Obtenemos longitud actual
        prefijo_actual = obtener_prefijo_actual(client_redis)
        longitud_actual = len(prefijo_actual) if prefijo_actual else len(PREFIJO_DEFECTO)

        longitud_nueva = longitud_actual
        if promedio < TIEMPO_OBJETIVO_BLOQUE * 0.8:
            # Se están minando demasiado rápido - más difícil
            longitud_nueva = min(longitud_actual + 1, LONGITUD_MAX_PREFIJO)
        elif promedio > TIEMPO_OBJETIVO_BLOQUE * 1.2:
            # Se están minando muy lento - más fácil
            longitud_nueva = max(longitud_actual - 1, LONGITUD_MIN_PREFIJO)

        nuevo_prefijo = "0" * longitud_nueva

        print(
            f"[dificultad] prefijo_actual='{prefijo_actual}' (len={longitud_actual}) -> "
            f"nuevo_prefijo='{nuevo_prefijo}' (len={longitud_nueva})",
            flush=True
        )

        # Actualizo métrica de longitud de prefijo
        LONGITUD_PREFIJO_ACTUAL.labels(COORDINATOR_ID).set(longitud_nueva)
        # Persisto en Redis
        client_redis.set("dificultad:prefijo_actual", nuevo_prefijo)
        return nuevo_prefijo

    except Exception as e:
        print(f"[dificultad] error ajustando dificultad: {e}", flush=True)
        prefijo_actual = obtener_prefijo_actual(client_redis)
        client_redis.set("dificultad:prefijo_actual", prefijo_actual)
        return prefijo_actual


# Bucket: subir/descargar block

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



# Endpoints Flask

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
    #  inicio de medición de tiempo de validación
    inicio_validacion = time.time()
    validacion_ok = False

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
       
        dur = time.time() - inicio_validacion
        TIEMPO_VALIDACION_BLOQUE_SEGUNDOS.observe(dur)
        BLOQUES_VALIDACION_ERROR.inc()
        try:
            push_to_gateway(
                f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
                job="coordinator",
                grouping_key={"instance": COORDINATOR_ID},
                registry=REGISTRY,
            )
        except Exception as e:
            print("[METRICAS] error al pushear (sin data):", repr(e), flush=True)

        return jsonify({'status': 'error', 'message': 'No data received'}), 400

    try:
        print(f"Received data: {data}", flush=True)
        bucket = bucketConnect(bucketName)
        block = descargarBlock(bucket, data['blockId'])

         
        # Métrica: tiempo de minado del worker
        
        try:
            processing_time = float(data.get("processingTime", 0.0))
        except (TypeError, ValueError):
            processing_time = 0.0

        tipo_worker   = str(data.get("workerType", "desconocido"))
        prefijo_bloque = str(block.get("prefijo", "desconocido"))

        if processing_time > 0:
            TIEMPO_MINADO_WORKER_SEGUNDOS.labels(
                tipo_worker,
                prefijo_bloque
            ).observe(processing_time)
            print(
                f"[METRICAS] tiempo_minado_worker_segundos: {processing_time:.4f}s "
                f"(tipo='{tipo_worker}', prefijo='{prefijo_bloque}')",
                flush=True
            )

        # dataHash = data['result'] + block['baseStringChain'] + block['blockchainContent']
        nonce   = str(data.get('result', ''))
        base    = str(block.get('baseStringChain', ''))
        content = str(block.get('blockchainContent') or "")   
        dataHash = nonce + base + content

        hashResult = calculateHash(dataHash)
        timestamp = time.time()
        print(f"[x] Hash recibido:  {data.get('hash')}", flush=True)
        print(f"[x] Hash calculado: {hashResult}", flush=True)

        if hashResult == data.get('hash'):
            print('[x] Hash OK » Data válida.', flush=True)

            if existBlock(block['blockId']):
                print('[x] Existe Bloque » DESCARTAR', flush=True)
                # bloque ya resuelto 
                validacion_ok = False
                respuesta = jsonify({'message': 'El bloque ya fue resuelto » DESCARTADO'})
                status_code = 200
            else:
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
                    newBlock['hashPrevio'] = ""
            
                # Armar bloque final
                newBlock['blockId']          = data['blockId']
                newBlock['hash']             = data['hash']
                newBlock['transactions']     = block['transactions']
                newBlock['prefijo']          = block['prefijo']
                newBlock['baseStringChain']  = block['baseStringChain']
                newBlock['timestamp']        = timestamp
                newBlock['nonce']            = data['result']
                newBlock['blockchainContent'] = newBlock['hashPrevio']

                print(f"[DEBUG] Guardando bloque validado en Redis", flush=True)
                postBlock(newBlock)
                print('[x] Bloque validado » Agregado a la blockchain', flush=True)

                validacion_ok = True
                respuesta = jsonify({'message': 'Bloque validado » Agregado a la blockchain'})
                status_code = 201
        else:
            print('[x] Hash inválido » DESCARTADO', flush=True)
            validacion_ok = False
            respuesta = jsonify({'message': 'El Hash recibido es inválido » DESCARTADO'})
            status_code = 200

    except Exception as e:
        print("[ERROR] excepción en /solved_task:", repr(e), flush=True)
        validacion_ok = False
        respuesta = jsonify({'status': 'error', 'message': 'Exception in solved_task'})
        status_code = 500

    #   registrar tiempo + contador + push a Pushgateway 
    dur = time.time() - inicio_validacion
    TIEMPO_VALIDACION_BLOQUE_SEGUNDOS.observe(dur)

    if validacion_ok:
        BLOQUES_VALIDACION_OK.inc()
        BLOQUES_VALIDACION_RESULTADO.labels(tipo_worker=tipo_worker,
                                        resultado="ok").inc()
    else:
        BLOQUES_VALIDACION_ERROR.inc()
        BLOQUES_VALIDACION_RESULTADO.labels(tipo_worker=tipo_worker,
                                        resultado="error").inc()

    try:
        push_to_gateway(
            f"{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}",
            job="coordinator",
            grouping_key={"instance": COORDINATOR_ID},
            registry=REGISTRY,
        )
        print(f"[METRICAS] push coordinator a {PUSHGATEWAY_HOST} OK (dur={dur:.3f}s)", flush=True)
    except Exception as e:
        print("[METRICAS] error al pushear metrics del coordinator:", repr(e), flush=True)

    return respuesta, status_code


# Loop: armar bloque y ENVIAR TAREA AL POOL

def processPackages():
    global connection, channel, datosBucket

    while True:
        try:
            contadorTransaction = 0
            print('[x] Buscando Transacciones', flush=True)
            print('---------------------------', flush=True)
            print('', flush=True)
            listaTransactions = []

            # Levantar hasta 20 transacciones por ciclo
            for _ in range(20):
                asegurar_rabbit_channel()
                with rabbit_lock:
                    method_frame, header_frame, body = channel.basic_get(queue=queueNameTx)

                if method_frame:
                    contadorTransaction += 1
                    listaTransactions.append(json.loads(body))
                    print(f"[x] Desencolé una transacción", flush=True)
                    print(f'[x] Transacción: {body}', flush=True)
                    print('', flush=True)

                    with rabbit_lock:
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

                # Prefijo de dificultad dinámico según historial de la blockchain
                prefijo = ajustar_dificultad(client)
                print(f"[x] Prefijo dinámico para el bloque {blockId}: '{prefijo}'", flush=True)

                block = {
                    "blockId": blockId,
                    "transactions": listaTransactions,
                    "prefijo": prefijo,
                    "baseStringChain": "A3F8",
                    "blockchainContent": getUltimoBlock()['blockchainContent'] if getUltimoBlock() else "0",
                    "numMaxRandom": maxRandom
                }

                print(f"blockchainContent: {block['blockchainContent']}", flush=True)

                # Guardar en bucket (persistimos el bloque “madre”)
                datosBucket.append(block)
                bucket = bucketConnect(bucketName)
                subirBlock(bucket, block)
                created = time.time()

                # Publicar tarea madre a la INBOX del POOL
                task = {
                    "type": "mine_block",
                    "taskId": blockId,
                    "range": {"min": 0, "max": maxRandom},
                    "block": block,
                    "created": created
                }

                asegurar_rabbit_channel()
                with rabbit_lock:
                    channel.basic_publish(
                        exchange=POOL_EXCHANGE,
                        routing_key=POOL_RK,
                        body=json.dumps(task),
                        properties=pika.BasicProperties(delivery_mode=2)  # persistente
                    )
                print(f'[x] Tarea {blockId} enviada a {POOL_QUEUE}', flush=True)
                print('', flush=True)

            time.sleep(timer)

        except (AMQPConnectionError, StreamLostError, ChannelWrongStateError) as e:
            print(f"[rabbit] error en processPackages: {e} -> reconectando...", flush=True)
            try:
                if connection and not connection.is_closed:
                    connection.close()
            except Exception:
                pass
            connection, channel = queueConnect()
            time.sleep(1)

        except Exception as e:
            print(f"[processPackages] excepción no controlada: {repr(e)}", flush=True)
            time.sleep(1)



connection, channel = queueConnect()
client = redisConnect()

status_thread = threading.Thread(target=processPackages)
status_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT','5000')))
