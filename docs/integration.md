# System Integration Guide

This document answers three questions:

1. What are all the parts of this system and how do they talk to each other?
2. Why does vault_setup.sh exist and why can't CI/CD run it?
3. What SHOULD CI/CD do, and what is missing right now?

---

## The five services

```
  Developer / Browser
        |
        | HTTPS (external)
        v
  [Inovar Portal]          <- external school system, not ours
        ^
        | Playwright browser automation (inside web container)
        |
  [web]  FastAPI + Uvicorn     :8000
        |             |
        | SQL          | hvac (HTTP)
        v             v
  [db]               [vault-server]
  PostgreSQL         HashiCorp Vault
  :5432              :8200
```

Each service lives in a Docker container. The `web` container is the only
one that talks outward (to Inovar). Everything else is internal.


### web — FastAPI application

Built from Dockerfile (two-stage: python:3.11-slim builder, then
mcr.microsoft.com/playwright/python:v1.44.0-jammy runner).

On startup the lifespan handler calls get_settings(). If VAULT_ADDR is set,
Settings.model_post_init calls VaultClient.login() then
VaultClient.read_inovar_credentials(). If Vault is unreachable at that
moment, the container exits immediately with:

    RuntimeError: Vault is sealed or unreachable — application cannot start.

This is intentional. A container that starts without valid credentials would
silently fail hours later on the first sync. Failing loud at boot is safer.

If VAULT_ADDR is not set, Settings reads INOVAR_USERNAME / INOVAR_PASSWORD
directly from the environment. This is the dev/test path.

Endpoint that does the actual work:

    POST /api/v1/horarios/sync?week=next|current

Spawns a Playwright Chromium browser inside the container, navigates to
Inovar, logs in, extracts the week schedule, writes rows to PostgreSQL, and
returns {"inserted": N, "skipped": N, "errors": N}.


### db — PostgreSQL 15

Standard postgres:15-alpine image. Persists data in the `postgres_data`
Docker volume so rows survive container restarts.

The `web` container has a depends_on/condition:service_healthy rule pointing
at db's healthcheck (pg_isready). Docker will not start `web` until
PostgreSQL is accepting connections.

Connection string used by SQLAlchemy:
    postgresql+asyncpg://postgres:postgres@db:5432/prof_db

The hostname `db` resolves because both containers share the `default`
bridge network created by docker-compose.yml.


### vault-server — HashiCorp Vault 1.15.4

Runs the Vault server process. Stores secrets in the `vault-data` Docker
volume using Raft integrated storage (no external Consul dependency).

Important: Vault has two states after a restart.

    sealed   — the encryption key is locked. Vault refuses ALL requests.
               The web container cannot start in this state.
    unsealed — normal operation. AppRole login works.

Vault does NOT unseal itself on restart. A human (or an operator tool with
the unseal keys) must run `vault operator unseal` after every Docker restart.
This is not a bug. It is the security model: if an attacker steals the disk
image (vault-data volume), they cannot read the secrets because the
encryption key is never written to disk.

The web container reaches Vault at http://vault:8200 (inter-container DNS
over prof-net). The host can reach it at http://localhost:8200.

The vault container's own VAULT_ADDR is set to http://0.0.0.0:8200, NOT
http://127.0.0.1:8200. This is a subtle but important distinction: the vault
CLI inside the container needs to reach the TCP listener bound to 0.0.0.0.
Using 127.0.0.1 inside a container where the listener is on 0.0.0.0 causes
the vault CLI healthcheck to fail even though the server is running fine.


### Playwright (inside web)

Not a separate container. The Playwright runtime lives inside the `web`
container because the Playwright base image ships Chromium and all its system
dependencies pre-installed. No `playwright install` step needed at runtime.

Playwright opens a real Chromium browser session, fills the Inovar login
form, and navigates to the schedule page. This is browser automation, not an
API call — Inovar does not expose an API.

The InovarScraperService constructor receives the password as SecretStr.
The only place .get_secret_value() is called is page.fill("#TRG_61", ...),
immediately before the keystroke. The plain string never touches any other
variable, log line, or attribute.


### Inovar (external)

The school's web portal at https://epralima.inovarmais.com. We do not control
it. We authenticate with a username and password and scrape the schedule page.

This is the only service that requires outbound internet from the web
container. Everything else is container-to-container.

---

## Network topology

Two Docker networks are involved.

    default (bridge, created by docker-compose.yml)
        web <-> db

    prof-net (external bridge, created manually once)
        web <-> vault-server

The split exists because Vault is managed by a separate Compose file
(docker-compose.vault.yml). Docker Compose creates a default bridge per file.
For two stacks to communicate by hostname they must share a network. prof-net
is that shared network, declared as external: true in both files.

If you only run docker-compose.yml (no Vault), the web container still starts
in env-only mode because VAULT_ADDR is empty. The prof-net network must still
exist on the host (docker network create prof-net) because docker-compose.yml
declares it — Docker will refuse to start the stack if an external network it
references does not exist.

    create prof-net once:  docker network create prof-net
    start Vault stack:     docker compose -f docker-compose.vault.yml up -d
    start app stack:       docker compose up -d


### Who talks to whom and on which network

    web  -> db           default bridge,  port 5432
    web  -> vault        prof-net,         port 8200  (hostname: vault)
    web  -> inovar       outbound internet, port 443
    host -> web          port forward,     port 8000
    host -> vault        port forward,     port 8200

db and vault never talk to each other.

---

## Credential flow at startup

This is the sequence that happens when you run `docker compose up -d` and the
web container boots:

    1. Docker starts db, waits until pg_isready passes.
    2. Docker starts web.
    3. Uvicorn calls app lifespan handler.
    4. Lifespan calls get_settings().
    5. Settings.__init__ reads env vars (VAULT_ADDR, VAULT_ROLE_ID,
       VAULT_SECRET_ID, INOVAR_USERNAME, INOVAR_PASSWORD, DATABASE_URL).
    6. Settings.model_post_init checks: is VAULT_ADDR set?
         No  -> done. INOVAR_USERNAME/PASSWORD came from env.
         Yes -> continue.
    7. VaultClient(vault_addr, role_id, secret_id.get_secret_value())
    8. VaultClient.login() -> POST http://vault:8200/v1/auth/approle/login
         Vault issues a short-lived token (20 min TTL).
    9. VaultClient.read_inovar_credentials()
         -> GET http://vault:8200/v1/secret/data/inovar/credentials
         Returns {"inovar_username": "...", "inovar_password": "..."}
    10. Settings overwrites inovar_username and inovar_password in-place.
        The INOVAR_* env vars are now irrelevant — Vault values win.
    11. lru_cache caches the Settings instance for the process lifetime.
    12. App is ready. /health returns {"status": "healthy", "vault": "connected"}.

If step 8 or 9 raises VaultUnavailableError or VaultAuthError, the lifespan
raises RuntimeError and the container exits. Docker will restart it
(restart: unless-stopped is set on the vault container; you may want to add
it to web too once Vault is stable).

---

## Why vault_setup.sh is a human operation, not CI/CD

This is the core of the integration question.

The setup script does two fundamentally different categories of work:

Category A — one-time operator actions (MUST be human):
    vault operator init       Generates unseal keys. Runs exactly once
                               per Vault installation. Output must be
                               saved to a password manager by a human.
    vault operator unseal     Requires possession of the unseal keys.
                               Runs after every Docker restart.
    vault kv put ...          Writing the real Inovar credentials into
                               Vault. A human types the password.

Category B — bootstrap actions (idempotent, could be scripted):
    docker network create     One-time host setup.
    vault secrets enable      Creates the KV engine mount.
    vault policy write        Writes inovar-policy.hcl.
    vault auth enable approle Enables the AppRole backend.
    vault write .../role      Creates the inovar-role.

Category A cannot run in CI/CD because:
- The unseal keys exist only in a password manager. CI/CD has no access to them
  and should never have access to them. If CI/CD could unseal Vault, then
  stealing the CI/CD secrets would give an attacker access to all credentials.
- vault operator init only runs once. Running it a second time would destroy
  all existing data.
- Credential input is interactive (Read-Host, read -s). CI/CD cannot type
  a password at a prompt.

What CI/CD DOES use from Vault:
- VAULT_ROLE_ID    — a non-secret identifier for the AppRole
- VAULT_SECRET_ID  — the credential that proves the role

These two values are stored as GitHub Actions secrets (or equivalent). CI/CD
never touches the root token or the unseal keys. The AppRole identity has a
read-only policy on exactly one Vault path. Even if VAULT_SECRET_ID leaks,
the blast radius is reading the Inovar credentials — not accessing Vault's
administration plane.

So the division is:
    Human operator runs once:  vault_setup.sh / vault_setup.ps1
    CI/CD uses forever:        VAULT_ROLE_ID + VAULT_SECRET_ID as secrets

---

## What CI/CD should do (and what is missing)

Right now there is no .github/workflows/ directory. This is the gap.

A complete CI/CD pipeline for this project has three jobs:

### Job 1: test (runs on every push)

    - Checkout
    - Install .venv dependencies from requirements.txt
    - Run pytest with --asyncio-mode=auto
    - No Vault needed: the test suite mocks VaultClient and uses SQLite
      in-memory for the database.
    - The 6 integration tests in test_vault_integration.py are skipped
      because VAULT_INTEGRATION_TEST is not set.

    Environment variables needed:
        none beyond what pytest sets up internally

### Job 2: integration-test (runs on push to main, optional)

    - Checkout
    - Start Vault in dev mode (vault server -dev) — dev mode auto-unseals
      and does not need operator init. It is safe for CI because it uses
      an in-memory backend that resets on every run.
    - Populate the KV path with test credentials (not real ones).
    - Set VAULT_INTEGRATION_TEST=1, VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID
      as CI environment variables (sourced from GitHub Actions secrets).
    - Run pytest tests/test_vault_integration.py.

    Environment variables needed (as GitHub Actions secrets):
        VAULT_ROLE_ID      from the output of vault/bootstrap.sh
        VAULT_SECRET_ID    from the output of vault/bootstrap.sh

### Job 3: deploy (runs on push to main, after tests pass)

    - Build the Docker image.
    - Push to a container registry (ghcr.io, Docker Hub, etc.).
    - SSH into the production host (or call a deployment API).
    - Pull the new image.
    - docker compose up -d --build web
      (Vault and db are NOT restarted — they are long-running stateful
       services that should not be touched by a code deploy.)

    Environment variables needed (as GitHub Actions secrets):
        DATABASE_URL
        VAULT_ADDR         (production Vault address)
        VAULT_ROLE_ID      (production AppRole role-id)
        VAULT_SECRET_ID    (production AppRole secret-id, rotated periodically)
        INOVAR_URL

None of these jobs ever touch the root token or the unseal keys.
Those never leave the password manager.

---

## Verifying each layer is healthy

Run these checks in order. Each one assumes the previous passed.

### 1. Docker network exists

    docker network inspect prof-net
    Expected: JSON with "Name": "prof-net"

### 2. Vault container is running

    docker ps | grep vault-server
    Expected: "Up X minutes" and "(healthy)" once the healthcheck passes

### 3. Vault is unsealed

    curl -s http://localhost:8200/v1/sys/health | python -m json.tool
    Expected: "sealed": false, "initialized": true

    If sealed: run vault_setup.sh --unseal-only (or vault_setup.ps1 -UnsealOnly)

### 4. Vault credentials are populated

    vault kv get secret/inovar/credentials
    Expected: table showing inovar_username and inovar_password rows

    If missing: vault kv put secret/inovar/credentials inovar_username=X inovar_password=Y

### 5. PostgreSQL is running

    docker ps | grep prof-horario-db
    Expected: "(healthy)"

    docker exec -it prof-horario-db-1 pg_isready -U postgres
    Expected: "accepting connections"

### 6. FastAPI container is running

    docker ps | grep prof-horario-web
    Expected: "Up"

    curl http://localhost:8000/health
    Expected: {"status": "healthy", "vault": "connected"}

    If vault is "not_configured": VAULT_ADDR is not reaching web.
    Check that .env has VAULT_ADDR=http://vault:8200 and that
    docker compose was restarted after editing .env.

    If the web container fails to start: check docker logs prof-horario-web-1
    The lifespan error message will say exactly which step failed.

### 7. Sync endpoint works

    curl -X POST "http://localhost:8000/api/v1/horarios/sync?week=next"
    Expected: {"inserted": N, "skipped": N, "errors": N}

    If you get 503 VAULT_UNAVAILABLE: Vault became sealed after startup.
    Unseal Vault and restart the web container.

    If you get 401 INOVAR_AUTH_ERROR: the credentials in Vault are wrong.
    Update them with vault kv put secret/inovar/credentials ...

    If you get 502 INOVAR_NAVIGATION_ERROR: Inovar's page structure changed
    or the portal is down. Check https://epralima.inovarmais.com manually.

---

## What breaks when each service goes down

    db goes down:
        web container stays running but any request that hits the database
        will return 500. The /health endpoint still returns 200 (it does not
        check the database). Add a db ping to /health if you want it to
        surface there.

    vault-server goes down (or becomes sealed):
        The web container keeps running because credentials were loaded at
        startup and are cached in memory (lru_cache on get_settings).
        Existing sessions continue working until the container is restarted.
        On next restart, web will fail to start until Vault is unsealed again.
        This is the correct behaviour: the app never retries Vault mid-flight.

    web goes down:
        All requests fail. db and vault are unaffected. docker compose up -d
        restarts it.

    Inovar portal goes down:
        The sync endpoint returns INOVAR_NAVIGATION_ERROR (502). No data is
        lost. PostgreSQL retains all previously synced rows. Retry when Inovar
        recovers.

---

## Files and their roles

    Dockerfile                  Two-stage build. Stage 1 installs Python deps.
                                Stage 2 uses Playwright base image for Chromium.

    docker-compose.yml          Starts web + db. Declares prof-net as external.

    docker-compose.vault.yml    Starts vault-server. Mounts vault-config.hcl.
                                Declares prof-net as external.

    vault/vault-config.hcl      Vault server config: Raft storage, TCP listener.

    vault/inovar-policy.hcl     Read-only policy on secret/data/inovar/*.

    vault/bootstrap.sh          One-time: creates KV mount, policy, AppRole.
                                Prints VAULT_ROLE_ID and VAULT_SECRET_ID.

    scripts/vault_setup.sh      Orchestrates all 9 setup steps. Idempotent.
                                Run once on a fresh machine, then --unseal-only
                                after every reboot.

    scripts/vault_setup.ps1     Same as above but PowerShell for Windows hosts.

    scripts/.vault-init.json    GITIGNORED. Created by vault operator init.
                                Contains unseal keys and root token.
                                Back this up to a password manager immediately.

    .env                        GITIGNORED. Contains VAULT_ROLE_ID,
                                VAULT_SECRET_ID, DATABASE_URL, INOVAR_URL.
                                Generated by vault_setup.sh step 8.

    .env.example                Committed. Shows all required keys with
                                placeholder values. No real credentials.

    app/config.py               Settings class. Reads env. Calls VaultClient
                                in model_post_init when VAULT_ADDR is set.

    app/services/vault_client.py  AppRole login + KV v2 read. Wraps hvac.
                                  Root token and secret-id never in repr.

    app/services/inovar_scraper.py  Playwright automation. Accepts SecretStr
                                    for password. Unwraps only at page.fill().

    app/main.py                 Lifespan: validates credentials at boot.
                                /health: reports vault status.
