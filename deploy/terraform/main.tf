terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  required_apis = [
    "run.googleapis.com",
    "bigtable.googleapis.com",
    "bigquery.googleapis.com",
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "geminidataanalytics.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.required_apis)
  service            = each.value
  disable_on_destroy = false
}

# --- Least-privilege runtime identity --------------------------------------
resource "google_service_account" "agent" {
  account_id   = var.agent_service_account_id
  display_name = "Cymbal Operations Coordinator Agent runtime identity"
}

resource "google_project_iam_member" "agent_roles" {
  for_each = toset([
    "roles/bigquery.jobUser",
    "roles/bigquery.dataViewer",
    "roles/bigtable.reader",
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.agent.email}"
}

# --- MCP Toolbox configuration held in Secret Manager -----------------------
resource "google_secret_manager_secret" "mcp_tools" {
  secret_id = "bigtable-mcp-tools-secret"
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "mcp_tools" {
  secret = google_secret_manager_secret.mcp_tools.id
  secret_data = templatefile("${path.module}/../../tools.yaml", {
    PROJECT_ID        = var.project_id
    BIGTABLE_INSTANCE = var.bigtable_instance
  })
}

# --- MCP Toolbox microservice (private, OIDC-authenticated) -----------------
resource "google_cloud_run_v2_service" "mcp_toolbox" {
  name     = var.mcp_service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.agent.email

    containers {
      image = var.mcp_image
      args = [
        "--tools-file=/app/tools.yaml",
        "--address=0.0.0.0",
        "--port=8080",
      ]
      ports {
        container_port = 8080
      }
      volume_mounts {
        name       = "tools-config"
        mount_path = "/app"
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }

    volumes {
      name = "tools-config"
      secret {
        secret = google_secret_manager_secret.mcp_tools.secret_id
        items {
          version = "latest"
          path    = "tools.yaml"
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_version.mcp_tools]
}

# Private by default: only the agent identity may invoke the MCP service.
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  name     = google_cloud_run_v2_service.mcp_toolbox.name
  location = google_cloud_run_v2_service.mcp_toolbox.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.agent.email}"
}

# --- Agent telemetry sink ----------------------------------------------------
resource "google_bigquery_dataset" "agent_telemetry" {
  dataset_id    = "agent_telemetry"
  location      = "US"
  friendly_name = "Coordinator agent evaluation and trace telemetry"
  depends_on    = [google_project_service.enabled]
}
