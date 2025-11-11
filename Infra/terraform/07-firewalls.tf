# resource "google_compute_firewall" "egress_to_ilb_http_https" {
#   name    = "egress-to-ilb-http-https"
#   network = google_compute_network.main.self_link

#   direction   = "EGRESS"
#   priority    = 1000
#   target_tags = ["worker-cpu"]

#   allow {
#     protocol = "tcp"
#     ports    = ["80", "443"]
#   }

#   # Si querés más específico, reemplazá por la IP privada del ILB cuando la tengas.
#   destination_ranges = ["10.0.0.0/18"] # subred "private"
# }
# Permitir SSH vía IAP a las VMs con tag "worker-cpu"
resource "google_compute_firewall" "allow_iap_ssh_workers" {
  name    = "allow-iap-ssh-workers"
  network = google_compute_network.main.name

  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["35.235.240.0/20"] # rango de IAP

  target_tags = ["worker-cpu"]        # el tag de tu Instance Template

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
