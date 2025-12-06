# HIT 7
## Ejecución:
```bash
./hit7.exe DESDE HASTA CADENA_COMIENZO STRING
```
## Problema con CUDA:
En la cursada de la asignatura, con los otros grupos observamos que, aunque se utilicen distintas placas con diferentes cantidades de núcleos CUDA, hay un punto en el cual las placas gráficas no pueden seguir calculando más debido a que se genera un error de recursos insuficientes. Aproximadamente el número máximo de iteración al que llegan es 90000. 

En el programa utilizamos 512 threads, y el bloque se calcula dependiendo del rango de números que se indicaron. Pero si el bloque es un número que muiltiplicado por 512 da más de 90000, entonces la ejecución da error. 

Por eso se recomienda probar el programa con un rango de valores menor o igual 85000.