# IMPORTA las IPs internas ya creadas
import {
  to = google_compute_address.coord_ilb
  id = "projects/sdypp092025/regions/us-east1/addresses/coord-ilb"
}

import {
  to = google_compute_address.rabbit_ilb
  id = "projects/sdypp092025/regions/us-east1/addresses/rabbit-ilb"
}

# (Si el SA 'kubernetes' ya existe)
import {
  to = google_service_account.kubernetes
  id = "projects/sdypp092025/serviceAccounts/kubernetes@sdypp092025.iam.gserviceaccount.com"
}
