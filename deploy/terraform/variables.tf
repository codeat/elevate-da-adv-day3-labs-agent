variable "project_id" {
  type        = string
  description = "Target GCP project id. Never hardcoded - supplied per environment."
}

variable "region" {
  type        = string
  description = "Compute/serving region for Cloud Run and Vertex AI Agent Runtime."
  default     = "us-central1"
}

variable "bigtable_instance" {
  type        = string
  description = "Cloud Bigtable instance backing the real-time cashier alert stream."
  default     = "operations-db"
}

variable "mcp_service_name" {
  type        = string
  description = "Cloud Run service name for the MCP Toolbox microservice."
  default     = "mcp-toolbox-bigtable"
}

variable "mcp_image" {
  type        = string
  description = "Official GCP Database Toolbox container image."
  default     = "us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest"
}

variable "agent_service_account_id" {
  type        = string
  description = "Least-privilege runtime service account id for the agent."
  default     = "cymbal-sa-data"
}
