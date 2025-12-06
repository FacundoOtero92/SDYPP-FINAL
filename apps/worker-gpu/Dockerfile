FROM nvidia/cuda:12.1.0-devel-ubuntu22.04

# Python y pip
RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiamos el código
COPY worker_gpu.py minero_gpu.py md5.cu cuda_md5.cu requirements.txt ./


# Instalamos dependencias de Python
RUN pip3 install --no-cache-dir -r requirements.txt

# Compilamos los programas CUDA (ajustá nombres si tu minero espera otros ejecutables)
RUN nvcc -O3 -o md5 md5.cu
RUN nvcc -O3 -o cuda_md5 cuda_md5.cu

# Entry point
CMD ["python3", "worker_gpu.py"]
