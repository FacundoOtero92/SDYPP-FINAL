resource "google_compute_instance" "pruebavm" {
  count         = var.instancias
  name          = "worker-cpu-${count.index}"
  machine_type  = var.tipo_vm
  zone          = var.zone

  boot_disk {
    initialize_params {
      image = var.imagen
    }
  }

locals {
  startup_script = templatefile("${path.module}/startup.sh.tftpl", {
    worker_env   = var.worker_env
    worker_image = var.worker_image
  })
}
  network_interface {
    network = "default"
    access_config {}
  }

 metadata = {
    startup-script = local.startup_script
  }

 
  # SA por si luego querés API GCP desde el container (opcional)
  service_account {
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}

# Managed Instance Group (el “pool”)
resource "google_compute_instance_group_manager" "worker_cpu_mig" {
  name               = "worker-cpu-mig"
  base_instance_name = "worker-cpu"
  zone               = var.zone

  version {
    instance_template = google_compute_instance_template.worker_cpu_tpl.self_link
  }

   target_size = var.pool_size   # tamaño del pool (lo cambiás con terraform apply)

  update_policy {
    minimal_action          = "REPLACE"
    type                    = "PROACTIVE"
    max_surge_fixed         = 1
    max_unavailable_fixed   = 0
  }
}

output "pool_name"  { value = google_compute_instance_group_manager.worker_cpu_mig.name }
output "tpl_name"   { value = google_compute_instance_template.worker_cpu_tpl.name }