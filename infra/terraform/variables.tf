variable "project_id" {
  description = "GCP Project ID (Hong Kong residency deployment)"
  type        = string
}

variable "region" {
  description = "GCP region (locked to asia-east2 Hong Kong for HK PDPO DPP3)"
  type        = string
  default     = "asia-east2"
}

variable "service_name" {
  type    = string
  default = "clinic-ai-harness-staging"
}

variable "sql_tier" {
  type    = string
  default = "db-f1-micro"
}

variable "container_image" {
  type = string
}
