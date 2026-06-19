#!/usr/bin/env bash
# scripts/vault_setup.sh
#
# Full Vault setup for prof-horario — run this once on a fresh machine.
#
# WHAT IT DOES
#   Step 1  Create the shared Docker network (prof-net)
#   Step 2  Start the Vault container (docker-compose.vault.yml)
#   Step 3  Wait for Vault to be reachable
#   Step 4  Initialize Vault — prints unseal keys + root token, saves them to
#           scripts/.vault-init.json (gitignored). BACK THIS FILE UP.
#   Step 5  Unseal Vault using the 3 keys from the init output
#   Step 6  Run vault/bootstrap.sh — KV engine, policy, AppRole
#   Step 7  Prompt for real Inovar credentials and write them to Vault
#   Step 8  Write VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID into .env
#   Step 9  Verify: run the integration tests
#
# USAGE
#   bash scripts/vault_setup.sh
#
# RE-RUNS
#   Safe to re-run. Each step checks whether it already completed and skips it
#   if so (idempotent). Useful after a reboot to just unseal (step 5).
#
# UNSEAL AFTER REBOOT
#   Vault wakes up sealed after every Docker restart. Run:
#     bash scripts/vault_setup.sh --unseal-only
#
# REQUIREMENTS
#   - Docker Desktop running
#   - docker compose v2 (docker compose, not docker-compose)
#   - vault CLI available on PATH  OR  run through docker exec (auto-detected)
#   - jq  (for parsing init output — installed automatically via winget if missing)

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}==>${RESET} ${BOLD}$*${RESET}"; }
success() { echo -e "${GREEN}OK${RESET}  $*"; }
warn()    { echo -e "${YELLOW}WARN${RESET} $*"; }
die()     { echo -e "${RED}ERR${RESET}  $*" >&2; exit 1; }

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INIT_OUTPUT="${SCRIPT_DIR}/.vault-init.json"
ENV_FILE="${REPO_ROOT}/.env"
BOOTSTRAP_SCRIPT="${REPO_ROOT}/vault/bootstrap.sh"

# ── Config ────────────────────────────────────────────────────────────────────
VAULT_CONTAINER="vault-server"
VAULT_ADDR="${VAULT_ADDR:-http://localhost:8200}"
NETWORK_NAME="prof-net"
UNSEAL_ONLY=false

# ── Arg parsing ───────────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --unseal-only) UNSEAL_ONLY=true ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

# ── vault CLI: prefer host binary, fall back to docker exec ──────────────────
vault_cmd() {
  if command -v vault &>/dev/null; then
    VAULT_ADDR="${VAULT_ADDR}" vault "$@"
  else
    docker exec -e VAULT_ADDR="http://0.0.0.0:8200" "${VAULT_CONTAINER}" vault "$@"
  fi
}

# ── jq check (needed for JSON parsing) ───────────────────────────────────────
check_jq() {
  if ! command -v jq &>/dev/null; then
    warn "jq not found — attempting install via winget..."
    winget install --id stedolan.jq -e --silent 2>/dev/null \
      || die "jq is required. Install manually: https://stedolan.github.io/jq/"
    export PATH="${PATH}:/c/ProgramData/chocolatey/bin"
  fi
}

# ── Step 1: Docker network ────────────────────────────────────────────────────
create_network() {
  info "Step 1: Ensure Docker network '${NETWORK_NAME}' exists"
  if docker network inspect "${NETWORK_NAME}" &>/dev/null; then
    success "Network '${NETWORK_NAME}' already exists"
  else
    docker network create "${NETWORK_NAME}"
    success "Network '${NETWORK_NAME}' created"
  fi
}

# ── Step 2: Start Vault container ─────────────────────────────────────────────
start_vault() {
  info "Step 2: Start Vault container"
  cd "${REPO_ROOT}"
  if docker ps --format '{{.Names}}' | grep -q "^${VAULT_CONTAINER}$"; then
    success "Vault container already running"
  else
    docker compose -f docker-compose.vault.yml up -d
    success "Vault container started"
  fi
}

# ── Step 3: Wait for Vault HTTP ───────────────────────────────────────────────
wait_for_vault() {
  info "Step 3: Wait for Vault to be reachable at ${VAULT_ADDR}"
  local retries=20
  until vault_cmd status &>/dev/null || [ "$retries" -eq 0 ]; do
    sleep 2
    retries=$((retries - 1))
    echo -n "."
  done
  echo ""
  # status exit code 2 = sealed (reachable but sealed) — that is fine here
  if vault_cmd status 2>/dev/null | grep -q "Initialized"; then
    success "Vault is reachable"
  elif [ "$retries" -eq 0 ]; then
    die "Vault did not become reachable in time. Check: docker logs ${VAULT_CONTAINER}"
  else
    success "Vault is reachable"
  fi
}

# ── Step 4: Initialize ────────────────────────────────────────────────────────
initialize_vault() {
  info "Step 4: Initialize Vault"

  local already_init
  already_init=$(vault_cmd status -format=json 2>/dev/null | jq -r '.initialized' 2>/dev/null || echo "false")

  if [ "${already_init}" = "true" ]; then
    success "Vault already initialized"
    if [ ! -f "${INIT_OUTPUT}" ]; then
      warn "Vault is initialized but ${INIT_OUTPUT} is missing."
      warn "You need your unseal keys to proceed. Cannot continue safely."
      die "Restore ${INIT_OUTPUT} from your backup (password manager) and re-run."
    fi
    return
  fi

  echo ""
  warn "About to initialize Vault. This generates unseal keys and a root token."
  warn "The output will be saved to: ${INIT_OUTPUT}"
  warn "BACK THIS FILE UP to a password manager immediately after this step."
  echo ""
  read -r -p "Press ENTER to continue or Ctrl+C to abort..."

  vault_cmd operator init \
    -key-shares=5 \
    -key-threshold=3 \
    -format=json > "${INIT_OUTPUT}"

  success "Vault initialized. Init output saved to ${INIT_OUTPUT}"
  echo ""
  warn "IMPORTANT: Back up ${INIT_OUTPUT} to a password manager RIGHT NOW."
  warn "If you lose the unseal keys, your Vault data is permanently inaccessible."
  echo ""
}

# ── Step 5: Unseal ────────────────────────────────────────────────────────────
unseal_vault() {
  info "Step 5: Unseal Vault"

  local sealed
  sealed=$(vault_cmd status -format=json 2>/dev/null | jq -r '.sealed' 2>/dev/null || echo "true")

  if [ "${sealed}" = "false" ]; then
    success "Vault is already unsealed"
    return
  fi

  if [ ! -f "${INIT_OUTPUT}" ]; then
    die "${INIT_OUTPUT} not found. Cannot unseal without keys. Restore from backup."
  fi

  local key1 key2 key3
  key1=$(jq -r '.unseal_keys_b64[0]' "${INIT_OUTPUT}")
  key2=$(jq -r '.unseal_keys_b64[1]' "${INIT_OUTPUT}")
  key3=$(jq -r '.unseal_keys_b64[2]' "${INIT_OUTPUT}")

  vault_cmd operator unseal "${key1}" > /dev/null
  vault_cmd operator unseal "${key2}" > /dev/null
  vault_cmd operator unseal "${key3}" > /dev/null

  sealed=$(vault_cmd status -format=json 2>/dev/null | jq -r '.sealed')
  if [ "${sealed}" = "false" ]; then
    success "Vault unsealed successfully"
  else
    die "Vault is still sealed after providing 3 keys. Check ${VAULT_CONTAINER} logs."
  fi
}

# ── Step 6: Bootstrap (KV, policy, AppRole) ───────────────────────────────────
bootstrap_vault() {
  info "Step 6: Bootstrap KV engine, policy, AppRole"

  local root_token
  root_token=$(jq -r '.root_token' "${INIT_OUTPUT}")

  # Check if AppRole is already enabled — if so, bootstrap already ran
  if VAULT_TOKEN="${root_token}" vault_cmd auth list -format=json 2>/dev/null \
      | jq -e '.["approle/"]' &>/dev/null; then
    success "Bootstrap already applied (AppRole already enabled)"
    return
  fi

  VAULT_TOKEN="${root_token}" bash "${BOOTSTRAP_SCRIPT}" "${root_token}"
  success "Bootstrap complete"
}

# ── Step 7: Write Inovar credentials ─────────────────────────────────────────
write_inovar_credentials() {
  info "Step 7: Write Inovar credentials to Vault"

  local root_token
  root_token=$(jq -r '.root_token' "${INIT_OUTPUT}")

  # Check if credentials already exist
  if VAULT_TOKEN="${root_token}" vault_cmd kv get \
      -format=json secret/inovar/credentials &>/dev/null; then
    success "Inovar credentials already present in Vault"
    echo ""
    warn "To update them run:"
    warn "  vault kv put secret/inovar/credentials inovar_username=X inovar_password=Y"
    return
  fi

  echo ""
  echo "Enter your Inovar credentials. They will be stored in Vault only — never in any file."
  echo ""
  read -r -p "  Inovar username: " INOVAR_USER
  read -r -s -p "  Inovar password: " INOVAR_PASS
  echo ""

  VAULT_TOKEN="${root_token}" vault_cmd kv put secret/inovar/credentials \
    inovar_username="${INOVAR_USER}" \
    inovar_password="${INOVAR_PASS}" > /dev/null

  success "Inovar credentials written to secret/inovar/credentials"
}

# ── Step 8: Write .env ────────────────────────────────────────────────────────
write_env_file() {
  info "Step 8: Write .env with Vault connection details"

  local root_token role_id secret_id
  root_token=$(jq -r '.root_token' "${INIT_OUTPUT}")

  role_id=$(VAULT_TOKEN="${root_token}" vault_cmd read \
    -field=role_id auth/approle/role/inovar-role/role-id)

  secret_id=$(VAULT_TOKEN="${root_token}" vault_cmd write \
    -force -field=secret_id auth/approle/role/inovar-role/secret-id)

  if [ -f "${ENV_FILE}" ]; then
    warn ".env already exists — creating .env.new instead to avoid overwriting"
    ENV_FILE="${REPO_ROOT}/.env.new"
  fi

  cat > "${ENV_FILE}" <<ENVEOF
# Generated by scripts/vault_setup.sh — do not commit.
# Vault mode — credentials are fetched from Vault at startup.
VAULT_ADDR=${VAULT_ADDR}
VAULT_ROLE_ID=${role_id}
VAULT_SECRET_ID=${secret_id}

# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/prof_db

# Inovar env-only fallback (leave blank when using Vault)
INOVAR_USERNAME=
INOVAR_PASSWORD=
INOVAR_URL=https://epralima.inovarmais.com/alunos/Inicial.wgx
ENVEOF

  success ".env written to ${ENV_FILE}"
}

# ── Step 9: Verify with integration tests ─────────────────────────────────────
run_integration_tests() {
  info "Step 9: Run Vault integration tests"
  cd "${REPO_ROOT}"

  local python_bin=".venv/Scripts/python.exe"
  [ -f "${python_bin}" ] || python_bin="python"

  if VAULT_INTEGRATION_TEST=1 VAULT_ADDR="${VAULT_ADDR}" \
      "${python_bin}" -m pytest tests/test_vault_integration.py \
      --asyncio-mode=auto -v; then
    success "All integration tests passed"
  else
    warn "Some integration tests failed — check the output above."
    warn "The app may still work; integration tests just verify the Vault wire-up."
  fi
}

# ── Summary ────────────────────────────────────────────────────────────────────
print_summary() {
  echo ""
  echo -e "${GREEN}${BOLD}================================================================${RESET}"
  echo -e "${GREEN}${BOLD} Vault setup complete!${RESET}"
  echo -e "${GREEN}${BOLD}================================================================${RESET}"
  echo ""
  echo "  Next steps:"
  echo "    1. Start the app:    docker compose up -d"
  echo "    2. Sync schedule:    curl -X POST http://localhost:8000/api/v1/horarios/sync"
  echo "    3. Check health:     curl http://localhost:8000/health"
  echo ""
  echo "  After any reboot, Vault wakes up SEALED. Unseal with:"
  echo "    bash scripts/vault_setup.sh --unseal-only"
  echo ""
}

# ── Entrypoint ────────────────────────────────────────────────────────────────
main() {
  echo ""
  echo -e "${BOLD}prof-horario Vault Setup${RESET}"
  echo "Vault address: ${VAULT_ADDR}"
  echo ""

  check_jq

  if "${UNSEAL_ONLY}"; then
    start_vault
    wait_for_vault
    unseal_vault
    success "Vault unsealed. You can now start the app."
    exit 0
  fi

  create_network
  start_vault
  wait_for_vault
  initialize_vault
  unseal_vault
  bootstrap_vault
  write_inovar_credentials
  write_env_file
  run_integration_tests
  print_summary
}

main
