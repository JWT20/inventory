terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 5.0"
    }
  }
}

# --- Variables ---
variable "tenancy_ocid" {}
variable "user_ocid" {}
variable "fingerprint" {}
variable "private_key_path" {}
variable "region" { default = "eu-amsterdam-1" }
variable "compartment_ocid" {}
variable "ssh_public_key_path" {}
variable "duckdns_domain" {}
variable "duckdns_token" {}
variable "openai_api_key" {}

# --- Provider ---
provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

# --- Data Sources ---
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# Get the latest Oracle Linux 9 aarch64 image (for Ampere A1)
data "oci_core_images" "ol9_aarch64" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Oracle Linux"
  operating_system_version = "9"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

# --- Networking ---
resource "oci_core_vcn" "wijnpick_vcn" {
  compartment_id = var.compartment_ocid
  display_name   = "wijnpick-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "wijnpick"
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.wijnpick_vcn.id
  display_name   = "wijnpick-igw"
  enabled        = true
}

resource "oci_core_route_table" "public_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.wijnpick_vcn.id
  display_name   = "wijnpick-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_security_list" "public_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.wijnpick_vcn.id
  display_name   = "wijnpick-public-sl"

  # Allow all egress
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }

  # SSH
  ingress_security_rules {
    protocol = "6" # TCP
    source   = "0.0.0.0/0"
    tcp_options {
      min = 22
      max = 22
    }
  }

  # HTTP
  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 80
      max = 80
    }
  }

  # HTTPS
  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_subnet" "public_subnet" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.wijnpick_vcn.id
  display_name      = "wijnpick-public-subnet"
  cidr_block        = "10.0.1.0/24"
  route_table_id    = oci_core_route_table.public_rt.id
  security_list_ids = [oci_core_security_list.public_sl.id]
  dns_label         = "public"
}

# --- Compute (Always Free Ampere A1) ---
resource "oci_core_instance" "wijnpick_vm" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "wijnpick-server"
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = 1
    memory_in_gbs = 6
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ol9_aarch64.images[0].id
    boot_volume_size_in_gbs = 50
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public_subnet.id
    assign_public_ip = true
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml", {
      duckdns_domain = var.duckdns_domain
      duckdns_token  = var.duckdns_token
      openai_api_key = var.openai_api_key
    }))
  }
}

# --- Outputs ---
output "vm_public_ip" {
  value = oci_core_instance.wijnpick_vm.public_ip
}

output "app_url" {
  value = "https://${var.duckdns_domain}.duckdns.org"
}

output "ssh_command" {
  value = "ssh opc@${oci_core_instance.wijnpick_vm.public_ip}"
}
