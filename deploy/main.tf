# OpenTofu config for Contabo (skeleton).
#
# The wijnpick VPS that is currently running was bootstrapped manually via
# the Contabo control panel and deploy/bootstrap.sh. It is NOT managed by
# this file. To provision a NEW VPS via OpenTofu (e.g. staging or a future
# replacement), uncomment the contabo_instance block below and run:
#
#   cp terraform.tfvars.example terraform.tfvars   # then fill in creds
#   tofu init
#   tofu plan
#   tofu apply
#
# After provisioning, scp deploy/bootstrap.sh to the new VPS and run it
# once as root to install Docker, Caddy, the deploy user, and UFW.

terraform {
  required_version = ">= 1.6"

  required_providers {
    contabo = {
      source  = "contabo/contabo"
      version = "~> 0.1"
    }
  }
}

variable "contabo_oauth2_client_id" {
  type      = string
  sensitive = true
}

variable "contabo_oauth2_client_secret" {
  type      = string
  sensitive = true
}

variable "contabo_oauth2_user" {
  type      = string
  sensitive = true
}

variable "contabo_oauth2_pass" {
  type      = string
  sensitive = true
}

provider "contabo" {
  oauth2_client_id     = var.contabo_oauth2_client_id
  oauth2_client_secret = var.contabo_oauth2_client_secret
  oauth2_user          = var.contabo_oauth2_user
  oauth2_pass          = var.contabo_oauth2_pass
}

# resource "contabo_instance" "wijnpick" {
#   display_name = "wijnpick"
#   product_id   = "V46"   # Cloud VPS 20 SSD; verify in Contabo panel before use
#   region       = "EU"
#   period       = 12
# }
#
# output "vps_ipv4" {
#   value = contabo_instance.wijnpick.ip_config[0].v4[0].ip
# }
