

variable "vpc_name" {
  description = "Nombre de la VPC"
  type        = string
  default     = "main" 
}

variable "subnet_name" {
  description = "Nombre de la subred en la región indicada"
  type        = string
  default     = "private" 
}

variable "dns_domain" {
  description = "Dominio DNS privado (debe terminar en punto)"
  type        = string
  default     = "svc.local."
}

variable "dns_zone_name" {
  description = "Nombre de la zona DNS privada"
  type        = string
  default     = "svc-zone"
}

variable "create_dns_zone" {
  description = "Crear (true) o reutilizar (false) la zona privada"
  type        = bool
  default     = true
}



# IP interna estática para ingress-nginx (ILB)

resource "google_compute_address" "ingress_ilb" {
  name         = "ingress-ilb"
  region       = var.region
  address_type = "INTERNAL"
  #  subnetwork   = google_compute_subnetwork.subnet.self_link
    # subnetwork   = google_compute_subnetwork.subnet.self_link
      
  subnetwork   = google_compute_subnetwork.private.self_link

}


# DNS privado (Cloud DNS)

resource "google_dns_managed_zone" "svc" {
  count      = var.create_dns_zone ? 1 : 0
  name       = var.dns_zone_name
  dns_name   = var.dns_domain
  visibility = "private"

  private_visibility_config {
    networks {
      # network_url = data.google_compute_network.vpc.self_link
     
      network_url = google_compute_network.main.self_link



    }
  }
}

locals {
  zone_name = var.create_dns_zone ? google_dns_managed_zone.svc[0].name : var.dns_zone_name
}

# A records para distintos nombres apuntando al mismo ILB
resource "google_dns_record_set" "coordinator_a" {
  name         = "coordinator.${var.dns_domain}"
  type         = "A"
  ttl          = 30
  managed_zone = local.zone_name
  rrdatas      = [google_compute_address.ingress_ilb.address]
}

resource "google_dns_record_set" "rabbitmq_a" {
  name         = "rabbitmq.${var.dns_domain}"
  type         = "A"
  ttl          = 30
  managed_zone = local.zone_name
  rrdatas      = [google_compute_address.ingress_ilb.address]
}

############################
# Outputs
############################
output "ingress_ilb_ip" {
  description = "IP interna del Ingress (ILB)"
  value       = google_compute_address.ingress_ilb.address
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
