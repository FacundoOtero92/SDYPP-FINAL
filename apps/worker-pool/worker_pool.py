import os, json, math, threading, traceback
from flask import Flask, jsonify, request
import pika, requests

app = Flask(__name__)

 # --- Config ---
RABBITMQ_HOST  = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER  = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS  = os.getenv("RABBITMQ_PASS", "guest")

POOL_EXCHANGE  = os.getenv("POOL_EXCHANGE", "coordinator.inbox")  # inbox del pool
POOL_QUEUE     = os.getenv("POOL_QUEUE", "pool.tasks")
POOL_RK        = os.getenv("POOL_RK", "tasks")

EXCHANGE_BLOCK = os.getenv("EXCHANGE_BLOCK", "ExchangeBlock")     # trabajo a workers
RK_GPU         = os.getenv("RK_GPU", "pow.gpu")
RK_CPU         = os.getenv("RK_CPU", "pow.cpu")

TTL_MS         = int(os.getenv("GPU_TTL_MS", "30000"))            # 30s por defecto
CHUNKS         = int(os.getenv("POOL_CHUNKS", "8"))               # cuántas partes cortar
COORDINATOR_URL= os.getenv("COORDINATOR_URL", "http://coordinador-integrador:5000")

def _conn():
    creds = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    return pika.BlockingConnection(pika.ConnectionParameters(
        host=RABBITMQ_HOST, port=5672, credentials=creds))

def declare_topology():
    """Declara inbox y colas de trabajo (idempotente)."""
    conn = _conn(); ch = conn.channel()

    # Inbox del pool
    ch.exchange_declare(exchange=POOL_EXCHANGE, exchange_type="direct", durable=True)
    ch.queue_declare(queue=POOL_QUEUE, durable=True, arguments={"x-queue-type": "quorum"})
    ch.queue_bind(exchange=POOL_EXCHANGE, queue=POOL_QUEUE, routing_key=POOL_RK)

    # Trabajo a workers: GPU (TTL→DLX a CPU) y CPU
    ch.exchange_declare(exchange=EXCHANGE_BLOCK, exchange_type="direct", durable=True)
    ch.queue_declare(
        queue=RK_GPU, durable=True,
        arguments={
            "x-queue-type": "quorum",
            "x-message-ttl": TTL_MS,
            "x-dead-letter-exchange": EXCHANGE_BLOCK,
            "x-dead-letter-routing-key": RK_CPU
        }
    )
    ch.queue_bind(exchange=EXCHANGE_BLOCK, queue=RK_GPU, routing_key=RK_GPU)

    ch.queue_declare(queue=RK_CPU, durable=True, arguments={"x-queue-type": "quorum"})
    ch.queue_bind(exchange=EXCHANGE_BLOCK, queue=RK_CPU, routing_key=RK_CPU)

    conn.close()
    print(f"[topology] inbox={POOL_QUEUE} gpu={RK_GPU}(ttl={TTL_MS}ms→dlx {RK_CPU}) cpu={RK_CPU}", flush=True)

def split_range(rmin: int, rmax: int, chunks: int):
    total = max(0, int(rmax) - int(rmin) + 1)
    chunks = max(1, int(chunks))
    step = max(1, math.ceil(total / chunks))
    parts = []
    cur = rmin
    while cur <= rmax:
        end = min(rmax, cur + step - 1)
        parts.append((cur, end))
        cur = end + 1
    return parts

def publish_subtask(ch, subtask: dict):
    ch.basic_publish(
        exchange=EXCHANGE_BLOCK,
        routing_key=RK_GPU,  # GPU-first; si expira, broker la manda a RK_CPU
        body=json.dumps(subtask),
        properties=pika.BasicProperties(delivery_mode=2)  # persistente
    )

def consume_inbox():
    declare_topology()
    conn = _conn(); ch = conn.channel()
    ch.basic_qos(prefetch_count=1)

    def _on_task(channel, method, props, body):
        try:
            task = json.loads(body)
            # Estructura esperada: {"type":"mine_block","taskId":...,"range":{"min":..,"max":..},"block":{...}}
            r = task.get("range", {"min": 0, "max": 999999})
            rmin, rmax = int(r.get("min", 0)), int(r.get("max", 0))
            parts = split_range(rmin, rmax, CHUNKS)

            for (a, b) in parts:
                sub = dict(task)
                sub["subRange"] = {"min": a, "max": b}
                publish_subtask(channel, sub)

            channel.basic_ack(delivery_tag=method.delivery_tag)
            print(f"[pool] task {task.get('taskId')} → {len(parts)} subtareas a {RK_GPU}", flush=True)
        except Exception as e:
            print("[pool][error] no se pudo procesar tarea:", e, traceback.format_exc(), flush=True)
            # requeue para reintentar
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    ch.basic_consume(queue=POOL_QUEUE, on_message_callback=_on_task, auto_ack=False)
    print("[pool] escuchando inbox:", POOL_QUEUE, flush=True)
    ch.start_consuming()

# --- HTTP API ---
@app.get("/alive")
def alive():
    return jsonify({"ok": True, "service": "worker-pool", "inbox": POOL_QUEUE}), 200

@app.post("/solved_task")
def solved_task():
    """Los workers (GPU/CPU) pueden postear acá; el pool reenvía al coordinator."""
    payload = request.get_json(force=True, silent=True) or {}
    try:
        r = requests.post(f"{COORDINATOR_URL}/solved_task", json=payload, timeout=10)
        return jsonify({"forwarded": True, "status": r.status_code}), 200
    except Exception as e:
        return jsonify({"forwarded": False, "error": str(e)}), 502

if __name__ == "__main__":
    # Consumidor en hilo aparte + servidor HTTP
    t = threading.Thread(target=consume_inbox, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5001)
