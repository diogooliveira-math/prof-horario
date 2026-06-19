#!/usr/bin/env bash
# vault/bootstrap.sh
#
# One-shot Vault bootstrap for the prof-horario project.
# Run ONCE after the very first 'vault operator init + unseal' sequence.
#
# Usage:
#   bash vault/bootstrap.sh <root-token>
#
# What it does:
#   1. Enables the KV v2 secrets engine mounted at 'secret/'
#   2. Writes the inovar policy from vault/inovar-policy.hcl
#   3. Enables AppRole auth
#   4. Creates the inovar-role tied to the inovar policy
#   5. Prints the role-id and secret-id — copy them into .env or CI secrets
#
# Prerequisites:
#   - Vault container is running and unsealed
#   - VAULT_ADDR is set (or defaults to http://127.0.0.1:8200)
#   - vault CLI is available (inside the container or on the host)
#
# Example (run from host with Vault port forwarded to 8200):
#   export VAULT_ADDR=http://127.0.0.1:8200
#   bash vault/bootstrap.sh hvs.XXXXXXXXXXXXXXXX

set -euo pipefail

ROOT_TOKEN="${1:-}"
if [[ -z "${ROOT_TOKEN}" ]]; then
  echo "ERROR: root token required as first argument" >&2
  echo "Usage: bash vault/bootstrap.sh <root-token>" >&2
  exit 1
fi

export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_TOKEN="${ROOT_TOKEN}"

echo "==> Authenticating with root token..."
vault token lookup --format=table | grep display_name

echo ""
echo "==> Step 1: Enable KV v2 secrets engine at 'secret/'"
# The mount may already exist on re-runs — ignore the error if so.
vault secrets enable -path=secret kv-v2 2>/dev/null || echo "    (already enabled)"

echo ""
echo "==> Step 2: Write inovar credentials (placeholder — replace with real values)"
# This creates the path that inovar-policy.hcl grants read on.
# Run this step manually with real credentials:
#   vault kv put secret/inovar/credentials inovar_username=YOUR_USER inovar_password=YOUR_PASS
echo "    Skipping credential write — populate manually:"
echo "    vault kv put secret/inovar/credentials inovar_username=REPLACE_ME inovar_password=REPLACE_ME"

echo ""
echo "==> Step 3: Write inovar-policy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
vault policy write inovar-policy "${SCRIPT_DIR}/inovar-policy.hcl"

echo ""
echo "==> Step 4: Enable AppRole auth"
vault auth enable approle 2>/dev/null || echo "    (already enabled)"

echo ""
echo "==> Step 5: Create inovar-role"
vault write auth/approle/role/inovar-role \
  secret_id_ttl="720h" \
  token_num_uses=0 \
  token_ttl="20m" \
  token_max_ttl="30m" \
  token_policies="inovar-policy"

echo ""
echo "==> Fetching role-id and secret-id..."
ROLE_ID=$(vault read -field=role_id auth/approle/role/inovar-role/role-id)
SECRET_ID=$(vault write -force -field=secret_id auth/approle/role/inovar-role/secret-id)

echo ""
echo "========================================================================"
echo "Bootstrap complete. Copy these values into your .env file or CI secrets:"
echo "========================================================================"
echo "VAULT_ADDR=${VAULT_ADDR}"
echo "VAULT_ROLE_ID=${ROLE_ID}"
echo "VAULT_SECRET_ID=${SECRET_ID}"
echo "========================================================================"
echo ""
echo "IMPORTANT: Store these values securely. The secret-id is single-use by"
echo "default and grants access to Inovar credentials. Do not commit to git."
