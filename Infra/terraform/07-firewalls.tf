resource "google_compute_firewall" "egress_to_ilb_http_https" {
  name    = "egress-to-ilb-http-https"
  network = google_compute_network.main.self_link

  direction   = "EGRESS"
  priority    = 1000
  target_tags = ["worker-cpu"]

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  # Si querés más específico, reemplazá por la IP privada del ILB cuando la tengas.
  destination_ranges = ["10.0.0.0/18"] # subred "private"
}
