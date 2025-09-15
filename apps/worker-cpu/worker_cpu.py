import pika
import json
import hashlib
import random
import requests
import time
import os

# hostRabbit = '104.196.215.66'
hostRabbit = os.getenv("RABBITMQ_HOST", "rabbitmq")
queueNameTx = 'QueueTransactions'
exchangeBlock = 'ExchangeBlock'

def calculateHash(data):
    hash_md5 = hashlib.md5()
    hash_md5.update(data.encode('utf-8'))
    return hash_md5.hexdigest()

def sendResult(data):
    # url = "http://localhost:5000/solved_task"
    # url = "http://35.227.79.99:5000/solved_task"
    coordinator_url = os.getenv("COORDINATOR_URL", "http://coordinador-integrador:5000/solved_task") 

    try:
        response = requests.post(coordinator_url, json=data)
        print("Post response:", response.text)
    except requests.exceptions.RequestException as e:
        print("Failed to send POST request:", e)

#   block = {
#                "blockId": blockId,
 #               "transactions": listaTransactions,
  #              "prefijo": '000',
   #             "baseStringChain": "A3F8",
    #            "blockchainContent": "contenido",
     #           "numMaxRandom": maxRandom 
      #      }


def on_message_received(ch, method, properties, body):
    data = json.loads(body)
    print(f"Message {data} received")
    print('')

    encontrado = False
    intentos   = 0
    startTime  = time.time()

    print("## Iniciando Minero ##")

    while not encontrado:
        intentos = intentos + 1
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

            print(f"[x] Hash con el prefijo {data['prefijo']} encontrado")
            print(f"[x] HASH: {hashCalculado}")
            print('')
            sendResult(dataResult) 
    
    ch.basic_ack(delivery_tag=method.delivery_tag)
    print(f"Result found and posted for block ID {data['blockId']} in {processingTime:.2f} seconds in {intentos} intentos")
    print(f"Resultado: {randomNumber}")
    print('')

def main():
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=hostRabbit,
        port=5672,
        credentials=pika.PlainCredentials("guest", "guest"),
    )
    )
    channel = connection.channel()
    channel.exchange_declare(exchange= exchangeBlock, exchange_type='topic', durable=True)
    result = channel.queue_declare('', exclusive=True)
    queue_name = result.method.queue
    channel.queue_bind(exchange= exchangeBlock, queue=queue_name, routing_key='blocks')
    channel.basic_consume(queue=queue_name, on_message_callback=on_message_received, auto_ack=False)
    print('Waiting for messages. To exit press CTRL+C')
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("Consumption stopped by user.")
        connection.close()
        print("Connection closed.")

if __name__ == '__main__':
    main()