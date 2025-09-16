



variable "network_self_link" {
  description = "Self link de la VPC donde viven la(s) VM(s) y el GKE"
  type        = string
}

variable "subnet_self_link" {
  description = "Self link de la subred donde exponen los ILB"
  type        = string
}

variable "dns_domain" {
  description = "Dominio DNS privado para servicios (debe terminar en punto)"
  type        = string
  default     = "svc.local."
}

variable "dns_zone_name" {
  description = "Nombre de la zona DNS privada"
  type        = string
  default     = "svc-zone"
}

variable "create_dns_zone" {
  description = "Crear la zona privada (true) o reutilizar una existente (false)"
  type        = bool
  default     = true
}

variable "coordinator_ilb_ip" {
  description = "IP interna estática para el Service coordinator (ILB)"
  type        = string
  default     = "10.142.0.50"
}

variable "rabbitmq_ilb_ip" {
  description = "IP interna estática para el Service rabbitmq (ILB)"
  type        = string
  default     = "10.142.0.42"
}

############################
# IPs internas estáticas (ILB)
############################

resource "google_compute_address" "coord_ilb" {
  name         = "coord-ilb"
  region       = var.region
  address_type = "INTERNAL"
  subnetwork   = var.subnet_self_link

}

resource "google_compute_address" "rabbit_ilb" {
  name         = "rabbit-ilb"
  region       = var.region
  address_type = "INTERNAL"
  subnetwork   = var.subnet_self_link

}



############################
# DNS privado (Cloud DNS)
############################

resource "google_dns_managed_zone" "svc" {
  count      = var.create_dns_zone ? 1 : 0
  name       = var.dns_zone_name
  dns_name   = var.dns_domain
  visibility = "private"

  private_visibility_config {
    networks {
      network_url = var.network_self_link
    }
  }
}

locals {
  zone_name = var.create_dns_zone ? google_dns_managed_zone.svc[0].name : var.dns_zone_name
}

# A-record: coordinator.svc.local.
resource "google_dns_record_set" "coordinator_a" {
  name         = "coordinator.${var.dns_domain}"
  type         = "A"
  ttl          = 30
  managed_zone = local.zone_name
  rrdatas      = [google_compute_address.coord_ilb.address]
}

# A-record: rabbitmq.svc.local.
resource "google_dns_record_set" "rabbitmq_a" {
  name         = "rabbitmq.${var.dns_domain}"
  type         = "A"
  ttl          = 30
  managed_zone = local.zone_name
  rrdatas      = [google_compute_address.rabbit_ilb.address]
}

############################
# Outputs
############################

output "coordinator_ilb_ip" {
  description = "IP interna estática del coordinator (ILB)"
  value       = google_compute_address.coord_ilb.address
}

output "rabbitmq_ilb_ip" {
  description = "IP interna estática de RabbitMQ (ILB)"
  value       = google_compute_address.rabbit_ilb.address
}

output "coordinator_fqdn" {
  description = "FQDN privado del coordinator"
  value       = "coordinator.${var.dns_domain}"
}

output "rabbitmq_fqdn" {
  description = "FQDN privado de RabbitMQ"
  value       = "rabbitmq.${var.dns_domain}"
}

output "dns_zone_used" {
  description = "Zona DNS utilizada"
  value       = local.zone_name
}
