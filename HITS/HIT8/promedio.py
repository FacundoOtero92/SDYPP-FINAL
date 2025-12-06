def calcular_promedio_tiempos(ruta_archivo):
    tiempos = []
    
    # Abrir el archivo y leer línea por línea
    with open(ruta_archivo, 'r') as archivo:
        for linea in archivo:
            # Buscar la palabra "Tiempo transcurrido:" y extraer el número después de ella
            if "Tiempo transcurrido:" in linea:
                tiempo_str = linea.split("Tiempo transcurrido: ")[1].split()[0]
                tiempo = float(tiempo_str)  # Convertir a float
                tiempos.append(tiempo)
    
    # Calcular el promedio
    promedio = sum(tiempos) / len(tiempos) if tiempos else 0
    return promedio

def main():
    # Ruta del archivo .txt
    ruta_archivo = 'time_output.txt'  # Asegúrate de poner la ruta correcta del archivo
    
    # Calcular el promedio de los tiempos
    promedio = calcular_promedio_tiempos(ruta_archivo)
    
    print(f"El promedio de los tiempos es: {promedio:.6f} segundos")

if __name__ == '__main__':
    main()
