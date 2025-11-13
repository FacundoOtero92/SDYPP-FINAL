data "google_container_cluster" "cluster-integrador" {
  name     = "cluster-integrador"
#  location = var.region # si es regional; si querés zonal, usa var.zone
 location = var.zone
  #  remove_default_node_pool = false
  #   initial_node_count       = 1
  #  deletion_protection = false

  # node_config {
  #   machine_type = "e2-standard-2"
  #   disk_type    = "pd-balanced"   # <- NO pd-ssd
  #   disk_size_gb = 20              # <- bajá a 20–30GB
  # service_account, oauth_scopes, labels, tags si aplica
  # }

  # Si NO usás Node Auto-Provisioning, podés omitir este bloque completo.
  # Si SÍ lo usás, forzá defaults sin SSD:
  # cluster_autoscaling {
  #   enabled = true
  #   auto_provisioning_defaults {
  #     disk_type    = "pd-balanced"
  #     disk_size_gb = 20
  #   }
  # }

  # network    = data.google_compute_network.vpc.self_link
  # subnetwork = data.google_compute_subnetwork.subnet.self_link

  # ip_allocation_policy {
  #   cluster_secondary_range_name  = "k8s-pod-range"
  #   services_secondary_range_name = "k8s-service-range"
  # }
}
