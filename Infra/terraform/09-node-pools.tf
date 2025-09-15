resource "google_service_account" "kubernetes" {
  account_id = "kubernetes"
}

resource "google_container_node_pool" "cpu_workers" {
  name     = "cpu-workers"
  cluster  = google_container_cluster.cluster-integrador.id
  location = var.zone

  autoscaling {
    min_node_count = 0
    max_node_count = 20
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = "e2-standard-4"
    spot         = true

    labels = {
      pool = "cpu-workers"
      team = "devops"
    }

    # Taint para que sólo tus worker-cpu con toleration se programen acá
    taint {
      key    = "workload"
      value  = "cpu"
      effect = "NO_SCHEDULE"
    }

    service_account = google_service_account.kubernetes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    metadata        = { disable-legacy-endpoints = "true" }
  }
}
