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


resource "google_compute_instance_template" "worker_cpu_tpl" {
  name_prefix  = "worker-cpu-"
  machine_type = var.machine_type
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
    network    = data.google_compute_network.vpc.id
    subnetwork = data.google_compute_subnetwork.subnet.id
    # SIN access_config {}  -> sin IP pública
  }

  tags = ["worker-cpu"]

  # ... metadata startup-script, scheduling, etc. como lo tenías
}

# Managed Instance Group = el pool
resource "google_compute_instance_group_manager" "worker_cpu_mig" {
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
}
