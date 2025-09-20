provider "google" {
  # credentials = file(var.credentials_file_path)
  project = var.project_id
  zone    = var.zone
  region  = var.region
}
terraform {
  backend "gcs" {
    bucket = "bucket_estados_tf"
    prefix = "infra/terraform/state" # carpeta dentro del bucket
  }
}
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "6.11" # por ej. 6.11.x; si no, 6.10.x
    }
  }
}
