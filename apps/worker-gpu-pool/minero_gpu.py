import subprocess
import json
import time
import os

# Ruta base: carpeta src
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "..", "bin")
OUT_FILE = os.path.join(BASE_DIR, "json_output.txt")  # lo dejamos en src

def ejecutar_minero(from_val, to_val, prefix, hash_val):
    # Reinicio el archivo de salida
    with open(OUT_FILE, 'w') as archivo:
        json.dump({"numero": 0, "hash_md5_result": ""}, archivo)

    # Rutas completas a los archivos de bin
    md5_cu = os.path.join(BIN_DIR, "md5.cu")
    md5_bin = os.path.join(BIN_DIR, "md5")

    # Comando para compilar el archivo CUDA
    compile_command = ['nvcc', md5_cu, '-o', md5_bin]

    compile_process = subprocess.run(compile_command, capture_output=True, text=True)
    if compile_process.returncode != 0:
        print("Error al compilar el archivo CUDA:")
        print(compile_process.stderr)
        return

    i = 0
    encontrado = False
    repeticiones = int(to_val / (512 * 150))
    desde = from_val
    print("repeticiones:", repeticiones)
    start_time_total = time.time()

    while i <= repeticiones and not encontrado:
        print("ciclos:", i, "comienzo:", desde)

        execute_command = [md5_bin, str(desde), str(to_val), prefix, hash_val]

        start_time = time.time()
        execute_process = subprocess.run(execute_command, capture_output=True, text=True)
        end_time = time.time()

        with open(OUT_FILE, 'r') as archivo:
            contenido = archivo.read()

        resultado = json.loads(contenido)
        execution_time = end_time - start_time

        if resultado.get('hash_md5_result'):
            encontrado = True

        desde += (512 * 150)
        i += 1

    end_time_total = time.time()
    execution_time_total = end_time_total - start_time_total
    print(execute_process.args)

    if execute_process.returncode != 0:
        print("No se encontro el resultado")
        print(execute_process.stderr)
        return

    print("Salida del programa minero:")
    print(execute_process.stdout)
    return contenido

if __name__ == "__main__":
    ejecutar_minero(1, 10000000, "00000", "PAPA")
