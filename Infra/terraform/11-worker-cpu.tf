# Render del startup script con variables
# Render del startup script con variables
locals {
  startup_script_raw = templatefile("${path.module}/startup.sh.tftpl", {
    worker_env   = var.worker_env
    worker_image = var.worker_image
  })

  # Normaliza EOL a LF (evita /bin/bash^M)
  startup_script = replace(local.startup_script_raw, "\r\n", "\n")
}


# Plantilla de instancia (UN template; NO uses count acá)
resource "google_compute_instance_template" "worker_cpu_tpl" {
  name_prefix  = "worker-cpu-"
  machine_type = var.machine_type


  service_account {
    email  = google_service_account.kubernetes.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
  lifecycle {
    create_before_destroy = true
  }

  disk {
    boot         = true
    auto_delete  = true
    source_image = var.imagen
    disk_size_gb = 20
  }

  network_interface {
    network = var.network
    access_config {} # IP pública (si no usás NAT)
  }

  tags = ["worker-cpu"]

  metadata = {
    startup-script = local.startup_script
  }

  scheduling {
    preemptible        = var.use_spot
    provisioning_model = var.use_spot ? "SPOT" : "STANDARD"
    automatic_restart  = var.use_spot ? false : true
  }


}

# Managed Instance Group = el pool
resource "google_compute_instance_group_manager" "worker_cpu_mig" {
  name               = "worker-cpu-mig"
  base_instance_name = "worker-cpu"
  zone               = var.zone

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
}
