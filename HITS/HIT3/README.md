# HIT 3
## https://github.com/nvidia/cccl : De que se trata el repositorio y cuando se actualizó
El repositorio de CUDA Core Compute Libraries (CCCL) centraliza y organiza tres bibliotecas fundamentales para el desarrollo en CUDA, diseñadas para aprovechar la computación en paralelo en GPUs. A continuación, un detalle de cada una:
- Thrust: Una biblioteca de algoritmos paralelos, que ofrece estructuras de datos y algoritmos de alto nivel, como reducción, ordenamiento, y transformaciones.
- CUB (CUDA UnBound): Proporciona primitivas de bajo nivel para operaciones paralelas, como escaneo, reducción y compresión, optimizadas para aprovechar al máximo la arquitectura de las GPUs.
- libcudacxx: Es la implementación de la biblioteca estándar de C++ diseñada específicamente para CUDA.
Este proyecto unifica las bibliotecas en un solo espacio para simplificar la colaboración entre desarrolladores. Además, facilita la administración de dependencias.

La ultima vez que fue actualizado fue: 11/12/2024 3:51PM.

## https://developer.nvidia.com/thrust : De que se trata
La página de Thrust en NVIDIA Developer describe una biblioteca de C++ optimizada para facilitar la programación paralela en arquitecturas heterogéneas, como GPUs y CPUs. 

Proporciona algoritmos paralelos como ordenación, reducción, escaneo y transformaciones, todos diseñados para aprovechar el procesamiento paralelo de manera eficiente.

Thrust ofrece estructuras de datos como thrust::device_vector y thrust::host_vector, que permiten gestionar datos de manera transparente entre la CPU y la GPU. Esto ayuda a los desarrolladores a escribir código más eficiente y portable, maximizando el rendimiento en entornos CUDA y simplificando la programación en C++ para aplicaciones de alto rendimiento.

## ¿Se necesita instalar algo adicional para ejecutar el primer ejemplo que se presenta en https://docs.nvidia.com/cuda/thrust/index.html#vectors ?
Lo unico que se requiere para la ejecución del ejemplo (según la página) es el NVIDIA HPC SDK y el CUDA toolkit. Con esto es suficiente para la ejecución del programa.

En nuestro caso, esto fue asi, no se necesito de instalar nada adicional para que funcione.

## https://docs.nvidia.com/cuda/archive/9.1/pdf/Thrust_Quick_Start_Guide.pdf : ¿Cuales son las diferencias de programar CUDA vs thrust/cccl?
- Abstracción: Thrust proporciona una capa de abstracción que simplifica el desarrollo al ocultar muchos de los detalles complejos de CUDA, mientras que la programación nativa de CUDA requiere manejar esos detalles explícitamente.
- Control: Con CUDA nativo hay más control sobre el rendimiento, que es importante en casos donde el tiempo es significativo. Con Thrust, aunque es más fácil de usar, se pierden las optimizaciones específicas.
- Facilidad de uso: Thrust facilita el desarrollo con su sintaxis más sencilla y funciones paralelas, mientras que CUDA nativo puede ser más difícil de aprender y de mantener debido a la mayor complejidad.