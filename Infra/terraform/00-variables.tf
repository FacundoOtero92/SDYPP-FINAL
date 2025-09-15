variable "region" {
  type    = string
  default = "us-east1"
}

variable "zone" {
  type    = string
  default = "us-east1-c"
}

variable "credentials_file_path" {

  type    = string
  default = "credentials.json"

}

variable "project_id" {

  type    = string
  default = "sdypp092025"

}


variable "instancias" {
  type    = number
  default = 1


}

variable "tipo_vm" {
  type    = string
  default = "e2-micro"

}

variable "imagen" {
  type    = string
  default = "ubuntu-os-cloud/ubuntu-2204-lts"
}
# variable "metadata_startup_script" {
#   type    = string
#   default = "../requeriments.sh"
# }
variable "metadata_startup_script" {
  type    = string
  default = "/Infra/terraform/startup.sh.tftpl"
}
#####Balancer    ####

variable "balancer_name" { default = "balancer" }

variable "base_instance_name" { default = "instancia" }

# Instance Template
variable "prefix" { default = "worker-" }
variable "desc" { default = "Worker que realiza funcion sobel." }
variable "tags" { default = "servicio" }
variable "desc_inst" { default = "worker sobel instance" }
variable "machine_type" { default = "n1-standard-1" }
variable "source_image" { default = "ubuntu-os-cloud/ubuntu-2204-lts" } //This is the family tag used when building the Golden Image with Packer.




variable "network" { default = "default" }


# Healthcheck
variable "hc_name" {
  type    = string
  default = "sobel-healthcheck"
}

variable "hc_port" {
  type    = string
  default = "80"
}


##backend
variable "be_name" { default = "http-backend" }
variable "be_protocol" { default = "HTTP" }
variable "be_port_name" { default = "http" }
variable "be_timeout" { default = "10" }
variable "be_session_affinity" { default = "NONE" }

# Global Forwarding Rule
variable "gfr_name" { default = "website-forwarding-rule" }
variable "gfr_portrange" { default = "80" }
variable "thp_name" { default = "http-proxy" }
variable "urlmap_name" { default = "http-lb-url-map" }
#
# Firewall Rules
variable "fwr_name" { default = "allow-http-https" }

#Autoscaler

variable "min_replicas" {
  type    = string
  default = 1
}

variable "max_replicas" {
  type    = string
  default = 10
}

###Rabbit 

variable "imagen_rabbit" {
  type    = string
  default = "rabbitimage2024"
}

variable "startup_rabbit" {
  type    = string
  default = "rabbit.sh"
}

##Redis

variable "imagen_redis" {
  type    = string
  default = "redis-image"
}

variable "startup_redis" {
  type    = string
  default = "redis.sh"
}
variable "startup_worker_cpu" {
  type    = string
  default = "worker_cpu.sh"
}


# ---- Imagen Docker en Docker Hub (pública) ----
variable "worker_image" {
  type    = string
  default = "docker.io/facundootero/worker-cpu:latest"
}

# ---- ENV que tu worker necesita (ajustalas a tu script) ----
variable "worker_env" {
  type = map(string)
  default = {
    RABBITMQ_USER     = "guest"
    RABBITMQ_PASSWORD = "guest"
    RABBITMQ_HOST     = "10.0.0.15"
    RABBITMQ_PORT     = "5672" # OJO: tu código debe castear a int si hace falta
    COORDINATOR_HOST  = "10.0.0.20"
    COORDINATOR_PORT  = "5000"
    KEEPALIVE_HOST    = "10.0.0.30"
    KEEPALIVE_PORT    = "5001"
    ES_WORKER_POOL    = "1" # "1" o "0" como string
  }
}
variable "pool_size" {
  type    = number
  default = 1
}
variable "use_spot" {
  type    = bool
  default = true
}
