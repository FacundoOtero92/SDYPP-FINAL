provider "google" {
  # credentials = file(var.credentials_file_path)
  project = var.project_id
  zone    = var.zone
  region  = var.region
}
terraform {
  backend "gcs" {
    bucket = "bucket_estados_tf"
    prefix = "infra/terraform/state"  # carpeta dentro del bucket
  }
}
