# Create Subnet in GCP using Terraform¶
# The next step is to create a private subnet to place Kubernetes nodes. 
# When you use the GKE cluster, the Kubernetes control plane is managed by Google,
# and you only need to worry about the placement of Kubernetes workers.

# https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/compute_subnetwork
resource "google_compute_subnetwork" "private" {
  name                     = "private"
  ip_cidr_range            = "10.0.0.0/18"
  region                   = "us-east1"
  network                  = google_compute_network.main.id
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "k8s-pod-range"
    ip_cidr_range = "10.48.0.0/14"
  }
  secondary_ip_range {
    range_name    = "k8s-service-range"
    ip_cidr_range = "10.52.0.0/20"
  }
}

# # NUEVA subred para las VMs externas en us-central1 (CIDR que no se solape)
# resource "google_compute_subnetwork" "main_uscentral1" {
#   provider      = google.uscentral
#   name          = "main-us-central1"
#   region        = local.vm_region          # "us-central1"
#   ip_cidr_range = "10.42.0.0/16"
#   network       = google_compute_subnetwork.main_uscentral1
# }

resource "google_compute_subnetwork" "main_uscentral1" {
  provider      = google.uscentral           # si definiste un provider alias
  name          = "main-us-central1"
  region        = "us-central1"
  ip_cidr_range = "10.42.0.0/16"
  network       = google_compute_network.main.id
}