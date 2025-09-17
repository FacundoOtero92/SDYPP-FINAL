#gcloud compute addresses create traefik-ip --global

resource "google_compute_global_address" "traefik_ip" {
  name = "traefik-ip"
}