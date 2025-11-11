
# https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/compute_network
resource "google_compute_network" "main" {
  name                            = "main"
  routing_mode                    = "REGIONAL"
  auto_create_subnetworks         = false
  mtu                             = 1460
  delete_default_routes_on_create = false

  depends_on = [
    google_project_service.compute,
    google_project_service.container
  ]





}
# resource "google_compute_subnetwork" "main_uscentral1" {
#   provider      = google.uscentral         # <— tu provider con alias
#   name          = "main-us-central1"
#   region        = var.vm_region          # "us-central1"
#   ip_cidr_range = "10.42.0.0/16"           # ajustá para no solapar con otras subredes
#   network       = google_compute_network.main.id
# }