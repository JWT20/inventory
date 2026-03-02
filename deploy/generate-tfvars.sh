#!/bin/bash
# Leest ~/.oci/config en genereert terraform.tfvars automatisch
# Gebruik: cd deploy && ./generate-tfvars.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OCI_CONFIG="${HOME}/.oci/config"
OUTPUT="${SCRIPT_DIR}/terraform.tfvars"

echo "=== WijnPick - terraform.tfvars generator ==="
echo ""

# --- Check OCI config ---
if [ ! -f "$OCI_CONFIG" ]; then
    echo "FOUT: ${OCI_CONFIG} niet gevonden!"
    echo "Configureer eerst de OCI CLI: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
    exit 1
fi

echo "OCI config gevonden: ${OCI_CONFIG}"
echo ""

# --- Parse OCI config (DEFAULT profile) ---
parse_oci() {
    local key="$1"
    grep -A 20 '^\[DEFAULT\]' "$OCI_CONFIG" | grep "^${key}" | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]'
}

TENANCY=$(parse_oci "tenancy")
USER_OCID=$(parse_oci "user")
FINGERPRINT=$(parse_oci "fingerprint")
KEY_FILE=$(parse_oci "key_file")
REGION=$(parse_oci "region")

# Expand ~ in key_file path
KEY_FILE="${KEY_FILE/#\~/$HOME}"

echo "Gevonden in OCI config:"
echo "  tenancy     = ${TENANCY}"
echo "  user        = ${USER_OCID}"
echo "  fingerprint = ${FINGERPRINT}"
echo "  key_file    = ${KEY_FILE}"
echo "  region      = ${REGION}"
echo ""

# --- Validate ---
MISSING=0
for var in TENANCY USER_OCID FINGERPRINT KEY_FILE REGION; do
    if [ -z "${!var}" ]; then
        echo "WAARSCHUWING: ${var} niet gevonden in OCI config"
        MISSING=1
    fi
done

if [ ! -f "$KEY_FILE" ] 2>/dev/null; then
    echo "WAARSCHUWING: Key file niet gevonden: ${KEY_FILE}"
fi

# --- Find SSH public key ---
SSH_PUB=""
for candidate in "${HOME}/.ssh/id_rsa.pub" "${HOME}/.ssh/id_ed25519.pub"; do
    if [ -f "$candidate" ]; then
        SSH_PUB="$candidate"
        break
    fi
done

if [ -z "$SSH_PUB" ]; then
    echo "WAARSCHUWING: Geen SSH public key gevonden in ~/.ssh/"
    SSH_PUB="${HOME}/.ssh/id_rsa.pub"
fi
echo "SSH public key: ${SSH_PUB}"
echo ""

# --- Prompt for missing values ---
read -p "DuckDNS subdomain (bijv. 'wijnpick'): " DUCKDNS_DOMAIN
read -p "DuckDNS token: " DUCKDNS_TOKEN
read -p "Gemini API key: " GEMINI_KEY
echo ""

# --- Compartment: default to tenancy (root) ---
read -p "Compartment OCID [Enter = root/tenancy]: " COMPARTMENT
COMPARTMENT="${COMPARTMENT:-$TENANCY}"

# --- Generate terraform.tfvars ---
cat > "$OUTPUT" << EOF
# Auto-gegenereerd door generate-tfvars.sh
# Bron: ${OCI_CONFIG}

tenancy_ocid     = "${TENANCY}"
user_ocid        = "${USER_OCID}"
fingerprint      = "${FINGERPRINT}"
private_key_path = "${KEY_FILE}"
region           = "${REGION}"

compartment_ocid = "${COMPARTMENT}"

ssh_public_key_path = "${SSH_PUB}"

# DuckDNS
duckdns_domain = "${DUCKDNS_DOMAIN}"
duckdns_token  = "${DUCKDNS_TOKEN}"

# Gemini
gemini_api_key = "${GEMINI_KEY}"
EOF

echo "=== terraform.tfvars gegenereerd! ==="
echo "Locatie: ${OUTPUT}"
echo ""
echo "Volgende stap:"
echo "  ./provision.sh"
