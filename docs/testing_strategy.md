# Testing Strategy

This document evaluates two external reflections on testing approach,
scores each claim against the actual codebase, and then states the real
testing strategy that is already in place and what still needs to be built.

---

## Evaluating reflection 1

The reflection proposes three testing dimensions: Unit, Integration,
Resilience. That framing is correct and matches what the project already
does, so it is a good map.

Where it breaks down is in the concrete code it shows.

### Problem 1 — wrong Settings interface

The example instantiates Settings like this:

    settings = Settings(vault_url="http://broken-vault:8200", vault_token="bad")
    settings.load_vault_secrets()

Neither of those things exist in this project.

The real Settings class (app/config.py) has:
    vault_addr: str       (not vault_url)
    vault_role_id: str
    vault_secret_id: SecretStr

There is no load_vault_secrets() method. Vault access happens automatically
inside model_post_init(), which pydantic-settings calls at construction time.
You cannot call it separately. There is no two-phase API.

The correct way to test a Vault failure is what test_config_vault.py already
does: patch app.config.VaultClient before constructing Settings(), and set
login.side_effect to the error you want. No vault_url, no vault_token,
no separate method call.

### Problem 2 — wrong exception for "Vault unreachable"

The example catches VaultDown:

    mocker.patch("hvac.Client.is_authenticated", side_effect=VaultDown())

VaultDown exists in hvac.exceptions (confirmed: hvac 2.4.0 installed). But
this project does not let hvac exceptions escape VaultClient. VaultClient.login()
catches ConnectionError and OSError (the real network errors) and translates
them into VaultUnavailableError. VaultDown is an hvac exception for a
different situation (Vault is running but in standby). The correct exception
to assert in tests is app.exceptions.VaultUnavailableError, not hvac's
VaultDown.

This is already correct in test_vault_client.py:

    def test_login_raises_vault_unavailable_on_connection_error():
        mock_hvac = _make_hvac_mock(login_raises=ConnectionError("refused"))
        with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
            vc = VaultClient(...)
            with pytest.raises(VaultUnavailableError):
                vc.login()

### Problem 3 — expects SystemExit, not a domain exception

    with pytest.raises(SystemExit):
        settings.load_vault_secrets()

The app does not call sys.exit() on a Vault failure. It raises VaultUnavailableError
from Settings.__init__(), which propagates up to the FastAPI lifespan handler,
which re-raises it as RuntimeError (so the container exits with a non-zero code
via uvicorn, not via sys.exit). Asserting SystemExit in a unit test is wrong —
it would always fail. The correct assertion is pytest.raises(VaultUnavailableError).

### Problem 4 — pytest-mock vs unittest.mock

The reflection uses mocker (from pytest-mock). This project uses unittest.mock
with patch(). Both work. pytest-mock is not in requirements.txt and is not
needed — do not add it just to match the reflection's style.

### What is genuinely good in reflection 1

The three-dimension framing (Unit / Integration / Resilience) maps directly
to what the project has:
- Unit tests: test_vault_client.py, test_config_vault.py — VaultClient and
  Settings fully mocked, no Docker, run in CI unconditionally.
- Integration tests: test_vault_integration.py — real Vault in dev mode,
  skipped unless VAULT_INTEGRATION_TEST=1.
- Resilience tests: not yet written, but the framing is correct.

The docker-compose.test.yml idea (a separate compose file for CI integration
tests) is also sound. See section "What still needs to be built" below.

---

## Evaluating reflection 2

This reflection correctly identifies two real problems in the
docker-compose.test.yml from reflection 1. Both critiques are accurate.

### Correct critique 1 — version key is deprecated

The version: '3.8' field in Compose files is deprecated as of Docker Compose
v2. Modern Docker engines print a warning and ignore it. The fixed
docker-compose.test.yml below omits it.

The existing docker-compose.vault.yml in this repo still has version: "3.8"
on line 1. That should be removed.

### Correct critique 2 — service_healthy with no healthcheck

If a service uses depends_on: condition: service_healthy but defines no
healthcheck block, Docker Compose fails immediately with:
    service "sut" depends on service "postgres_test" which does not have a
    healthcheck configured
The fixed config in reflection 2 adds healthcheck blocks to both
postgres_test and vault_test. That is correct.

### Problem 1 — vault healthcheck command is wrong

The fixed config in reflection 2 uses:

    healthcheck:
      test: ["CMD", "vault", "status"]

vault status exits with code 2 when Vault is sealed (running but not yet
unsealed). In dev mode Vault auto-unseals, so status exits 0. But this
healthcheck will fail on any non-dev Vault until it is unsealed — including
during the few seconds before dev mode finishes initializing. The safer
command is:

    test: ["CMD", "sh", "-c", "vault status || vault status -address=http://0.0.0.0:8200"]

or simply tolerate exit code 2 in the healthcheck with a shell wrapper:

    test: ["CMD-SHELL", "vault status || [ $? -eq 2 ]"]

Exit code 2 = sealed but reachable, which for a test fixture starting in dev
mode is a transient state. The healthcheck should pass it.

### Problem 2 — VAULT_TOKEN in docker-compose.test.yml does not match what VaultClient needs

The reflection sets:

    environment:
      - VAULT_URL=http://vault_test:8200
      - VAULT_TOKEN=test-token

But this project does not have a VAULT_TOKEN or VAULT_URL setting. Settings
reads VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID. The sut service needs
those three env vars, not VAULT_TOKEN. The test suite does AppRole login —
it does not use a root token directly.

The fixed config below uses the correct variable names.

### Problem 3 — the sut container runs pytest against the wrong test set

    command: pytest /app/tests

This would run ALL tests including test_vault_integration.py. Those
integration tests need VAULT_INTEGRATION_TEST=1 to run, and they connect
to a Vault at VAULT_TEST_ADDR, not VAULT_ADDR. The sut command needs to:
- set VAULT_INTEGRATION_TEST=1
- set VAULT_TEST_ADDR to the vault_test container's address
- target only tests/test_vault_integration.py (or the whole suite with the
  correct env so integration tests are not skipped)

### What is genuinely good in reflection 2

- Removing version: '3.8' — correct, do it.
- Adding healthcheck to postgres_test — correct and required.
- Adding healthcheck to vault_test — correct, with the fixed command.
- The --exit-code-from sut flag — essential. Without it docker compose up
  always exits 0 regardless of whether pytest passed.
- The && docker compose down after the run — good practice for CI cleanup.

---

## The real testing strategy in this project

### Layer 1: Unit tests (no Docker, run always)

These run on every git push. No env vars required beyond what pytest sets up.

    .venv/Scripts/python.exe -m pytest --asyncio-mode=auto -q

Files:
    tests/test_vault_client.py      9 tests — VaultClient with mocked hvac
    tests/test_config_vault.py      9 tests — Settings with mocked VaultClient
    tests/test_secret_discipline.py 5 tests — SecretStr is never unwrapped early
    tests/test_infra_files.py      27 tests — required files exist with correct content
    tests/test_startup_lifespan.py  6 tests — lifespan errors and /health responses
    tests/test_horario.py           N tests — router and scraper with mocked DB
    tests/test_sync_endpoint.py     N tests — POST /sync response contract

What is being tested at this layer:
- VaultClient raises the right domain exception for each hvac failure mode.
- Settings constructs VaultClient with the correct arguments.
- Settings overwrites inovar credentials from Vault when vault_addr is set.
- Settings does not call VaultClient when vault_addr is absent.
- inovar_password is SecretStr; get_secret_value() is called only at page.fill().
- vault_secret_id is SecretStr; it does not appear in repr or logs.
- The lifespan handler converts VaultUnavailableError into RuntimeError with
  a human-readable message.
- /health returns vault: "connected" when vault_addr is set.

### Layer 2: Integration tests (real Vault in dev mode, opt-in)

These test the actual wire between VaultClient and a real Vault HTTP server.
They skip unless VAULT_INTEGRATION_TEST=1.

Run locally (requires Docker):

    docker run --rm -d --name vault-test \
      -p 8300:8200 \
      -e VAULT_DEV_ROOT_TOKEN_ID=root-test-token \
      hashicorp/vault:1.15.4 \
      server -dev -dev-listen-address=0.0.0.0:8200

    set VAULT_INTEGRATION_TEST=1
    set VAULT_TEST_ADDR=http://localhost:8300
    set VAULT_TEST_ROOT_TOKEN=root-test-token

    .venv/Scripts/python.exe -m pytest tests/test_vault_integration.py \
      --asyncio-mode=auto -v

    docker stop vault-test

File:
    tests/test_vault_integration.py     6 tests

What is being tested at this layer:
- Vault dev mode is reachable and authenticated.
- Writing a secret via admin hvac client, reading it back via VaultClient wrapper.
- AppRole login succeeds with a valid role-id and secret-id.
- An AppRole-scoped token (read-only policy) can read inovar/credentials.
- The same token cannot write — verifying the policy is tight.
- End-to-end: Settings() with real VAULT_* env vars reads live credentials.

Why dev mode and not a full Vault with operator init?
Dev mode starts already initialized and unsealed, with in-memory storage.
No unseal keys, no persistent state, resets on restart. It is the correct
tool for ephemeral test fixtures. The production Vault (used by the app at
runtime) uses Raft storage, is manually initialized once, and requires a
human to unseal after every restart. Those are different concerns.

### Layer 3: Resilience tests (not yet written)

These validate what happens when infrastructure breaks after startup — not
at startup, which layers 1 and 2 already cover.

Three scenarios worth testing:

Scenario A — Vault goes down after startup.
The web container keeps running because get_settings() is lru_cache'd.
A token issued at startup lasts 20 minutes (token_ttl in bootstrap.sh).
After token expiry, the next sync that reaches VaultClient.read_inovar_credentials()
will fail with VaultAuthError (Forbidden). The sync endpoint should catch
that and return a 503, not a 500 traceback.
Currently the sync endpoint does NOT re-read from Vault mid-flight
(credentials were loaded at boot). This is correct and intentional.
There is no secret rotation implemented. If you add rotation, this layer
of testing becomes mandatory.

Scenario B — PostgreSQL restarts mid-flight.
SQLAlchemy's asyncpg connection pool does not auto-reconnect by default.
A pool_pre_ping=True setting in the engine creation tells SQLAlchemy to
test a connection before using it, replacing stale ones transparently.
Check whether the engine in app/database.py has pool_pre_ping set.
If not, a database restart silently breaks all subsequent requests.

Scenario C — Inovar portal is slow.
Playwright has a default timeout of 30 seconds per navigation. If Inovar
is responding but very slowly, a sync will hang for 30 seconds then raise
InovarNavigationError. No action needed unless you want to tune the timeout.

Resilience tests are not automated in the current test suite. They are manual
operator scenarios documented in docs/integration.md under the verification
checklist.

---

## The docker-compose.test.yml to build (not yet in the repo)

This file does not exist yet. Here is the correct version, incorporating the
valid fixes from reflection 2 and correcting the errors found above.

```yaml
# docker-compose.test.yml
#
# Spins up postgres_test + vault_test + sut (system under test).
# The sut container runs pytest and exits.
# Docker Compose returns the exit code of sut.
#
# Run with:
#   docker compose -f docker-compose.test.yml up --build --exit-code-from sut
#   docker compose -f docker-compose.test.yml down -v
#
# Note: version key intentionally omitted — deprecated in Compose v2.

services:
  postgres_test:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: test_user
      POSTGRES_PASSWORD: test_password
      POSTGRES_DB: test_db
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U test_user -d test_db"]
      interval: 3s
      timeout: 3s
      retries: 10

  vault_test:
    image: hashicorp/vault:1.15.4
    environment:
      VAULT_DEV_ROOT_TOKEN_ID: "root-test-token"
      VAULT_DEV_LISTEN_ADDRESS: "0.0.0.0:8200"
    cap_add:
      - IPC_LOCK
    healthcheck:
      # Exit code 2 = sealed but reachable — also acceptable for dev mode startup.
      test: ["CMD-SHELL", "vault status -address=http://0.0.0.0:8200 || [ $$? -eq 2 ]"]
      interval: 3s
      timeout: 3s
      retries: 10

  sut:
    build:
      context: .
      dockerfile: Dockerfile
    command: >
      python -m pytest tests/test_vault_integration.py
      --asyncio-mode=auto -v
    environment:
      - DATABASE_URL=postgresql+asyncpg://test_user:test_password@postgres_test:5432/test_db
      - VAULT_ADDR=http://vault_test:8200
      - VAULT_ROLE_ID=
      - VAULT_SECRET_ID=
      - VAULT_INTEGRATION_TEST=1
      - VAULT_TEST_ADDR=http://vault_test:8200
      - VAULT_TEST_ROOT_TOKEN=root-test-token
      - INOVAR_USERNAME=
      - INOVAR_PASSWORD=
      - INOVAR_URL=https://example.com
    depends_on:
      postgres_test:
        condition: service_healthy
      vault_test:
        condition: service_healthy
```

Three things to note about this file:

1. postgres_test uses postgres:15-alpine to match the production docker-compose.yml
   (postgres:16 from reflection 1 is a gratuitous version bump with no benefit).

2. VAULT_ROLE_ID and VAULT_SECRET_ID are intentionally empty. The integration
   tests use the admin hvac client with the root dev token directly — they do
   not go through AppRole login except in the tests that specifically test AppRole.
   The test fixture in test_vault_integration.py sets up its own AppRole role
   via vault_client_admin and reads the role_id/secret_id from Vault at runtime.

3. The file is not committed yet. It requires deciding where the integration
   tests run in CI (Job 2 in the CI/CD plan in docs/integration.md). Create
   it when setting up GitHub Actions.

---

## What is missing right now

In priority order:

1. Remove version: "3.8" from docker-compose.vault.yml (deprecated, causes
   warnings on every docker compose command).

2. Add pool_pre_ping=True to the SQLAlchemy engine in app/database.py so
   database reconnects work after a PostgreSQL restart.

3. Add .github/workflows/ci.yml — the unit test job (Job 1 from
   docs/integration.md). This is the most impactful missing piece: right now
   no automated check runs on every push.

4. Create docker-compose.test.yml (from the template above) and wire it to
   Job 2 in the CI workflow.

5. Write the resilience tests for Scenario B (database reconnect) once
   pool_pre_ping is confirmed working.
