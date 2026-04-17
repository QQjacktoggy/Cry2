# Binance Futures Bot - GCP Infrastructure
# Usage: terraform init && terraform plan && terraform apply

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "asia-east1"
}

variable "zone" {
  description = "GCP Zone"
  type        = string
  default     = "asia-east1-b"
}

variable "ssh_allowed_ips" {
  description = "IP addresses allowed for SSH"
  type        = list(string)
  default     = []
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Static External IP
resource "google_compute_address" "bot_ip" {
  name   = "binance-bot-ip"
  region = var.region
}

# Persistent Disk for data
resource "google_compute_disk" "data_disk" {
  name  = "binance-bot-data"
  type  = "pd-balanced"
  zone  = var.zone
  size  = 50
}

# Compute Engine VM
resource "google_compute_instance" "bot_vm" {
  name         = "binance-bot"
  machine_type = "e2-small"
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = 30
    }
  }

  attached_disk {
    source      = google_compute_disk.data_disk.id
    device_name = "data-disk"
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.bot_ip.address
    }
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  service_account {
    scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
    ]
  }

  tags = ["binance-bot"]
}

# Firewall: SSH
resource "google_compute_firewall" "ssh" {
  name    = "binance-bot-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = length(var.ssh_allowed_ips) > 0 ? var.ssh_allowed_ips : ["0.0.0.0/0"]
  target_tags   = ["binance-bot"]
}

# Firewall: Dashboard
resource "google_compute_firewall" "dashboard" {
  name    = "binance-bot-dashboard"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["8501"]
  }

  source_ranges = length(var.ssh_allowed_ips) > 0 ? var.ssh_allowed_ips : ["0.0.0.0/0"]
  target_tags   = ["binance-bot"]
}

# GCS Bucket for backups
resource "google_storage_bucket" "backup" {
  name     = "${var.project_id}-bot-backup"
  location = var.region

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  uniform_bucket_level_access = true
}

# Secret Manager secrets
resource "google_secret_manager_secret" "binance_api_key" {
  secret_id = "binance-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "binance_api_secret" {
  secret_id = "binance-api-secret"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "telegram_bot_token" {
  secret_id = "telegram-bot-token"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "telegram_chat_id" {
  secret_id = "telegram-chat-id"
  replication {
    auto {}
  }
}

# Outputs
output "vm_ip" {
  value = google_compute_address.bot_ip.address
}

output "vm_name" {
  value = google_compute_instance.bot_vm.name
}

output "backup_bucket" {
  value = google_storage_bucket.backup.name
}
