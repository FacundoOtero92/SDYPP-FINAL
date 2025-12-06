#include <stdio.h>

__global__ void holamundo_cuda() {
    int bloque_tamanio = blockDim.x;
    int bloque_numero = blockIdx.x;
    int thread_numero = threadIdx.x;
    
    int id_ejecucion = bloque_numero * bloque_tamanio + thread_numero;
    printf("Hola mundo desde ejecucion numero %i\n", id_ejecucion);
}

int main() {
    int bloque_cantidad = 1;
    int bloque_tamanio = 896;

    holamundo_cuda<<<bloque_cantidad, bloque_tamanio>>>();
    cudaDeviceSynchronize();
}

