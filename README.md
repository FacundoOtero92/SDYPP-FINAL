# **Trabajo Práctico Integrador – SDYPP- Curso 2024**
**Autor:** Facundo Otero  
**Legajo:** 118048   
**Correo:** facundo_otero@hotmail.com 

--- 

## 📄 Documentación
### Informe Final
 *Agregar aquí el enlace al PDF del informe final*

### Diagrama de Arquitectura
 https://drive.google.com/file/d/1dVnFhrbgs3D4V9aQI--b5QqKiuRAHx2U/view?usp=sharing


---

## 📌 Descripción General
Este repositorio contiene el desarrollo completo del Trabajo Práctico Integrador de SDYPP 2024**.  
El proyecto implementa una plataforma blockchain distribuida, con un Coordinador, un Worker Pool, Workers CPU desplegados en un MIG y Workers GPU externos.  
El sistema utiliza RabbitMQ, Redis, Google Cloud Storage.

Toda la arquitectura está desplegada en Google Kubernetes Engine (GKE) utilizando Terraform y pipelines CI/CD con GitHub Actions.  
Además, cuenta con monitoreo completo mediante Prometheusy Grafana.

---


## 🧱 Componentes del Sistema

### **Coordinador**
- Arma los bloques.
- Ajusta la dificultad de forma dinámica.
- Valida los resultados enviados por los workers.

### **Worker Pool**
- Recibe tareas de minado y las distribuye entre los workers.

### **Workers CPU (MIG) y GPU externos**
- Ejecutan el minado del bloque.
- Reportan heartbeats y tiempos de procesamiento.
- GPU con fallback a CPU en caso de falla.

### **Mensajería y Almacenamiento**
- **RabbitMQ:** manejo de colas y distribución de tareas.
- **Redis:** almacenamiento de la blockchain y control de duplicados.
- **Google Cloud Storage:** persistencia de bloques “madre”.

### **Monitoreo**
- **Prometheus + Grafana**
- Métricas por tipo de worker, dificultad, latencia, validación, etc.
- Dashboard accesible vía NGINX.

---

## ☁️ Infraestructura en Google Cloud
- Cluster GKE Autopilot.
- Namespaces:
  - `apps`
  - `servicios`
  - `monitoreo`
  - `ingress-nginx`
- Ingress NGINX para exponer las aplicaciones.
- MIG para Workers CPU.
- Workers GPU desplegados.
- Terraform.
- GitHub Actions para CI/CD.



---

##  Tecnologías Principales
- Python 
- Flask
- CUDA (para Worker GPU)
- Redis, RabbitMQ
- Kubernetes (GKE)
- Terraform
- GitHub Actions
- Grafana / Prometheus
- Docker

---
