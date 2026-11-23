output "cloud_run_url" {
  value = google_cloud_run_v2_service.harness.uri
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.postgres.connection_name
}

output "vault_bucket" {
  value = google_storage_bucket.vault.name
}

output "vault_kms_key" {
  value = google_kms_crypto_key.vault_kek.id
}
