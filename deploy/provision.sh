#!/bin/bash
# Provision WijnPick on Oracle Cloud Infrastructure
# Vereist: terraform, OCI CLI geconfigureerd in ~/.oci/config
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== WijnPick OCI Provisioning ==="
echo ""

# Check prerequisites
command -v terraform >/dev/null 2>&1 || { echo "Terraform is niet geinstalleerd. Installeer via: brew install terraform"; exit 1; }

if [ ! -f terraform.tfvars ]; then
    echo "terraform.tfvars niet gevonden!"
    echo "Kopieer terraform.tfvars.example naar terraform.tfvars en pas de waarden aan:"
    echo "  cp terraform.tfvars.example terraform.tfvars"
    exit 1
fi

echo "1/3 Terraform init..."
terraform init

echo ""
echo "2/3 Terraform plan..."
terraform plan

echo ""
read -p "Doorgaan met provisioning? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Geannuleerd."
    exit 0
fi

echo ""
echo "3/3 Terraform apply..."
until terraform apply -auto-approve; do
    echo ""
    echo "Terraform apply mislukt (waarschijnlijk 'Out of host capacity')."
    echo "Opnieuw proberen over 2 minuten... ($(date))"
    sleep 120
done

echo ""
echo "=== Provisioning compleet! ==="
echo ""
terraform output
echo ""
echo "De VM is aan het opstarten. Wacht ~3 minuten voor cloud-init."
echo "Daarna: kopieer de app-bestanden naar de VM met deploy/setup.sh"
