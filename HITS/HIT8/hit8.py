import hashlib
import time

def calcular_hash(texto):
    hash = hashlib.md5()
    hash.update(texto.encode('utf-8'))
    return hash.hexdigest()

def hit8(desde, hasta, prefix, cadena):
    encontrado = False
    start_time = time.time()
    count = desde
    while count<=hasta:
        number = str(count)
        hash_calculado = calcular_hash(f"{number}{cadena}")
        if hash_calculado.startswith(str(prefix)):
            encontrado = True
            number_encontrado = number
            ultimo_hash_encontrado = hash_calculado
            processing_time = time.time() - start_time
        count+=1
    if (encontrado):
        print(f"Se encontró el hash: {ultimo_hash_encontrado}")
        print(f"Se utilizó: {number_encontrado}{cadena} para obtenerlo")
        print(f"Tiempo: {processing_time}")
    else: 
        print("No se encontró hash")
        processing_time = 0
    return processing_time

import sys

def main():
    # Verificar si se recibieron 4 parámetros
    if len(sys.argv) != 5:
        print("Uso: python script.py <from> <to> <prefix> <string>")
        sys.exit(1)
    
    # Obtener los argumentos
    from_value = int(sys.argv[1])
    to_value = int(sys.argv[2])
    prefix = sys.argv[3]
    string = sys.argv[4]

    hit8(from_value, to_value, prefix, string)


if __name__ == "__main__":
    main()
