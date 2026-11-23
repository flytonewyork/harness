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

locals {
  required_apis = toset([
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "aiplatform.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudkms.googleapis.com",
  ])
  runtime_roles = toset([
    "roles/cloudsql.client",
    "roles/storage.objectAdmin",
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
    "roles/cloudkms.cryptoKeyEncrypterDecrypter",
    "roles/logging.logWriter",
  ])
  secret_names = toset([
    "clinic-harness-db-url",
    "clinic-harness-db-admin-url",
    "clinic-harness-jwt-secret",
    "clinic-harness-vault-kek",
    "clinic-harness-vault-signing",
  ])
}

resource "google_project_service" "required" {
  for_each           = local.required_apis
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "harness" {
  location      = var.region
  repository_id = "tmrw-harness"
  format        = "DOCKER"
  description   = "Clinic AI Harness container images (Hong Kong asia-east2)"
  depends_on    = [google_project_service.required]
}

resource "google_kms_key_ring" "vault" {
  name       = "tmrw-vault-keyring"
  location   = var.region
  depends_on = [google_project_service.required]
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
  depends_on = [google_project_service.required]
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
  depends_on = [google_project_service.required]
}

resource "google_sql_database" "clinic_harness" {
  name     = "clinic_harness"
  instance = google_sql_database_instance.postgres.name
}

resource "google_service_account" "runtime" {
  account_id   = "tmrw-acute-runtime"
  display_name = "Clinic AI Harness Least-Privilege Runtime"
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = local.runtime_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret" "app_secrets" {
  for_each  = local.secret_names
  secret_id = each.value
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
  depends_on = [google_project_service.required]
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
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "VERTEX_REGION"
        value = var.region
      }
      env {
        name  = "VAULT_GCS_BUCKET"
        value = google_storage_bucket.vault.name
      }
      env {
        name  = "OUTBOX_WORKER_ENABLED"
        value = "true"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app_secrets["clinic-harness-db-url"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "DATABASE_ADMIN_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app_secrets["clinic-harness-db-admin-url"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "JWT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app_secrets["clinic-harness-jwt-secret"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "VAULT_KEK_B64"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app_secrets["clinic-harness-vault-kek"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "VAULT_SIGNING_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app_secrets["clinic-harness-vault-signing"].secret_id
            version = "latest"
          }
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
  }
  depends_on = [google_project_iam_member.runtime_roles]
}
