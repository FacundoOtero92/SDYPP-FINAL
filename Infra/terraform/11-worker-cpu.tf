# locals: OK
locals {
  startup_script_raw = templatefile("${path.module}/startup.sh.tftpl", {
    worker_env   = var.worker_env      # map(string) con tus vars (RABBIT..., etc.)
    worker_image = var.worker_image    # p.ej: "docker.io/tuuser/worker-cpu:latest"
  })
  # Normaliza EOL a LF
  startup_script = replace(local.startup_script_raw, "\r\n", "\n")
}

# Template de instancia
resource "google_compute_instance_template" "worker_cpu_tpl" {
  provider    = google.uscentral
  name_prefix = "worker-cpu-"
  machine_type = var.machine_type

  # Imagen base: usar Debian/Ubuntu (apt)
  disk {
    boot         = true
    auto_delete  = true
    source_image = var.imagen           # ej: "projects/debian-cloud/global/images/family/debian-12"
    disk_size_gb = 20
    type         = "pd-balanced"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main_uscentral1.self_link
    # SIN access_config {}  -> sin IP pública (asegurate de reachability al Rabbit por la VPC)
  }

  # STARTUP SCRIPT: **ESTE ES EL PUNTO CLAVE**
  metadata_startup_script = local.startup_script

  # (Opcional) metadata útil para depurar
  metadata = {
    enable-oslogin       = "TRUE"
    serial-port-enable   = "TRUE"   # para ver stacktrace del arranque en "View serial port"
  }

  # (Recomendado) Service account + scopes si tu worker usa APIs de GCP
 service_account {
  email  = "default"  # usa la Default Compute SA del proyecto
  scopes = ["https://www.googleapis.com/auth/cloud-platform"]
}

  tags = ["worker-cpu"]

  # Shielded VM por defecto (está bien dejarlo)
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  lifecycle {
    create_before_destroy = true
  }
}

# MIG
resource "google_compute_instance_group_manager" "worker_cpu_mig" {
  provider           = google.uscentral
  name               = "worker-cpu-mig"
  base_instance_name = "worker-cpu"
  zone               = "us-central1-c"

  version {
    instance_template = google_compute_instance_template.worker_cpu_tpl.self_link
  }

  target_size = var.pool_size

  update_policy {
    minimal_action        = "REPLACE"
    type                  = "PROACTIVE"
    max_surge_fixed       = 1
    max_unavailable_fixed = 0
  }
 depends_on = [
    google_compute_router_nat.nat_all,     # NAT listo antes de crear VMs
    google_compute_router.nat_router
  ]
  # (Opcional) autohealing si exponés un puerto/healthcheck
  # auto_healing_policies {
  #   health_check      = google_compute_health_check.worker_hc.self_link
  #   initial_delay_sec = 60
  # }
}

# Router en la misma región y VPC
resource "google_compute_router" "nat_router" {
  name    = "nat-router-uscentral1"
  region  = "us-central1"
  network = google_compute_network.main.self_link
}

# Cloud NAT para todas las subredes de la región
resource "google_compute_router_nat" "nat_all" {
  name                               = "nat-all-uscentral1"
  router                             = google_compute_router.nat_router.name
  region                             = google_compute_router.nat_router.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
