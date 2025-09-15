# Render del startup script con variables
locals {
  startup_script = templatefile("${path.module}/startup.sh.tftpl", {
    worker_env   = var.worker_env
    worker_image = var.worker_image
  })
}

# Plantilla de instancia (UN template; NO uses count acá)
resource "google_compute_instance_template" "worker_cpu_tpl" {
  name_prefix  = "worker-cpu-"
  machine_type = var.machine_type

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

  service_account {
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
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
