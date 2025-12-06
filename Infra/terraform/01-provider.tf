provider "google" {
  # credentials = file(var.credentials_file_path)
  project = var.project_id
  zone    = var.zone
  region  = var.region
}

provider "google" {
  alias   = "uscentral"             // <-- alias para las VMs externas
  project = var.project_id
  region  = local.vm_region
  zone    = var.vm_zone
}
terraform {
  backend "gcs" {
    bucket = "bucket_estados"
    prefix = "infra/terraform/state" # carpeta dentro del bucket
  }
}
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "6.11" 
    }
  }
}
