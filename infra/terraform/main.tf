# Hong Kong (asia-east2) Infrastructure-as-Code for Clinic AI Harness (HK PDPO DPP3/DPP4 aligned)
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_artifact_registry_repository" "harness" {
  location      = var.region
  repository_id = "tmrw-harness"
  format        = "DOCKER"
  description   = "Clinic AI Harness container images (Hong Kong asia-east2)"
}

resource "google_kms_key_ring" "vault" {
  name     = "tmrw-vault-keyring"
  location = var.region
}

resource "google_kms_crypto_key" "vault_kek" {
  name            = "tmrw-vault-kek"
  key_ring        = google_kms_key_ring.vault.id
  rotation_period = "7776000s" # 90-day automatic KEK rotation (ISO 27799)
}

resource "google_storage_bucket" "vault" {
  name                        = "${var.project_id}-tmrw-vault-hk"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
}

resource "google_sql_database_instance" "postgres" {
  name             = "tmrw-acute-pg15"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier = var.sql_tier
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "19:00"
    }
    ip_configuration {
      ssl_mode = "ENCRYPTED_ONLY"
    }
  }
}

resource "google_sql_database" "clinic_harness" {
  name     = "clinic_harness"
  instance = google_sql_database_instance.postgres.name
}

resource "google_service_account" "runtime" {
  account_id   = "tmrw-acute-runtime"
  display_name = "Clinic AI Harness Least-Privilege Runtime"
}

resource "google_cloud_run_v2_service" "harness" {
  name     = var.service_name
  location = var.region

  template {
    service_account = google_service_account.runtime.email
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.postgres.connection_name]
      }
    }
    containers {
      image = var.container_image
      env {
        name  = "HARNESS_REGION"
        value = var.region
      }
      env {
        name  = "VAULT_GCS_BUCKET"
        value = google_storage_bucket.vault.name
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
  }
}
