output "mcp_service_url" {
  value       = google_cloud_run_v2_service.mcp_toolbox.uri
  description = "Export this as BIGTABLE_MCP_URL in your environment."
}

output "agent_service_account" {
  value       = google_service_account.agent.email
  description = "Least-privilege runtime identity for Agent Runtime deployment."
}
