# HashiCorp Vault — Study Guide, Reflection Evaluation, and Integration Plan

This document has three parts.

Part 1 is a conceptual introduction to Vault — what it is, why it exists,
and the core primitives you need to understand before touching any config.

Part 2 is an honest evaluation of the reflection you received. It separates
what is technically correct from what is over-engineering for this project,
what is missing, and what is actively wrong.

Part 3 is the concrete integration plan for prof-horario: how Vault plugs
into the existing `app/config.py`, `docker-compose.yml`, and the Inovar
scraper — with TDD steps and exact test assertions.

---

## Part 1 — What Vault Is and Why It Matters

### The problem Vault solves

Right now the project works like this:

    .env file on disk
      └─> pydantic-settings reads INOVAR_USERNAME / INOVAR_PASSWORD
            └─> Settings.inovar_password (SecretStr)
                  └─> InovarScraperService constructor

This is fine for a solo developer. The problem appears the moment you ask
any of these questions:

- Can I prove that only the `web` container ever read the password, and
  not some other process on the same host?
- If I rotate the Inovar password, how do I update it without restarting
  the container and touching the host filesystem?
- If a colleague joins, how do I give them access to the dev credentials
  without emailing a plaintext password?
- Can I audit exactly who (or which service) accessed the credential
  and when?

A `.env` file answers none of these. Vault answers all of them.

### The mental model: Vault as a locked safe with a guard

Vault is a process that holds secrets encrypted at rest. It exposes a
single HTTP API. Nobody reads a secret directly from disk — they ask the
API, prove who they are, and the API returns what that identity is allowed
to see.

Three concepts drive everything:

  Secrets engine
    A plugin that knows how to store or generate secrets.
    KV v2 (key-value, version 2) is the one we use: it stores arbitrary
    key/value pairs and keeps a full version history so you can roll back.

  Auth method
    How a caller proves its identity to Vault.
    AppRole is the auth method for machines and containers: it issues a
    role-id (public, like a username) and a secret-id (private, like a
    one-time password). Exchange both for a short-lived token.

  Policy
    A Vault policy is an ACL. It maps a path pattern to a set of
    capabilities (read, write, list, delete).
    Example: `path "secret/data/inovar/*" { capabilities = ["read"] }`
    This says: anyone whose token was issued under this policy can read
    secrets stored under that path prefix, and nothing else.

### The unseal ritual

Vault encrypts its own storage using a master key. That master key is
never stored anywhere — instead it is split into N shares using Shamir
Secret Sharing, and you need at least a threshold T of those shares to
reconstruct it. When Vault starts up it is "sealed" (cannot read its own
storage) until T shares are provided.

This is the single most operationally annoying fact about Vault: every
time the process restarts, it wakes up sealed, and someone (or something)
has to unseal it before the application can boot.

In development there is an escape hatch: `vault server -dev`. Dev mode
starts Vault already initialized, unsealed, with a fixed root token
printed to stdout. Nothing is persisted across restarts. It is useless
for production but perfect for unit and integration tests.

---

## Part 2 — Evaluation of the Reflection

### What the reflection gets right

The core Vault primitives are described accurately.

  Raft integrated storage is the correct choice.
    File-based Consul storage (the old default) requires a separate
    Consul cluster. Raft is built into Vault itself, needs no external
    dependency, and is what HashiCorp now recommends for single-node
    deployments.

  AppRole is the right auth method for containerized applications.
    Using the root token inside an application container is equivalent
    to running your app as root. AppRole issues tokens with a short TTL
    scoped to exactly one policy. When the token expires the app must
    re-authenticate — this limits the blast radius of a stolen token.

  `cap_add: IPC_LOCK` is required.
    Without it, the OS can swap Vault's memory pages to disk, potentially
    writing raw plaintext secrets to your swap partition. With IPC_LOCK
    the process pins its memory pages and this cannot happen.

  KV v2 is the right secrets engine for static credentials.
    KV v2 keeps a version history. If you rotate the Inovar password and
    the scraper breaks, you can read the previous version to understand
    what changed. KV v1 has no history.

  The production checklist items are all real.
    Audit logging, TLS on the listener, revoking the root token after
    setup, and Raft snapshots for backup are all genuine production
    requirements, not cargo cult.

### What the reflection gets wrong

  `VAULT_ADDR=http://127.0.0.1:8200` in the container's own environment.
    This is incorrect. `127.0.0.1` inside a container is the container's
    own loopback. The Vault container cannot reach itself via its own
    listener at that address — or rather it can for `vault` CLI commands
    run inside the container, but the `web` container cannot reach
    `127.0.0.1:8200` on a different container.
    For the `web` container to reach Vault, `VAULT_ADDR` must be
    `http://vault:8200` (using Docker Compose service name resolution).

  key-shares=5, threshold=3 for a single developer.
    The reflection recommends a 5-of-3 Shamir split. This is correct for
    a team of 5 people who hold one key each. For a single-developer
    personal project it produces 5 unseal keys that all live in the same
    person's password manager, which defeats the purpose. For this project
    `key-shares=1 key-threshold=1` is the honest choice for dev/personal
    use. The split can be increased if the project ever has multiple
    operators.

  The unseal-on-restart problem is not mentioned at all.
    This is the biggest practical gap. Every time `docker compose restart`
    or a host reboot occurs, Vault wakes up sealed. Your FastAPI container
    will fail its startup health checks because `app/config.py` cannot
    reach Vault yet. The reflection says nothing about this.
    Solutions in increasing order of complexity:
      a. Accept it — unseal manually after each restart (fine for dev).
      b. Write a small unseal script that runs as an init container.
      c. Use Vault's auto-unseal via a cloud KMS (AWS KMS, GCP CKMS) —
         this requires a cloud account.
    For this project, option (a) is correct now. The doc should be honest
    about that rather than pretending a single `restart: unless-stopped`
    solves it.

  The Python application side is completely absent.
    The reflection explains every Vault CLI command but never shows:
      - which Python library to use (`hvac`)
      - how `app/config.py` changes to read from Vault instead of `.env`
      - how the FastAPI startup sequence changes
      - what happens when Vault is unreachable at boot time
    Without this, the reflection is a Vault operations manual, not an
    integration guide for this project.

  The existing `docker-compose.yml` is ignored.
    The reflection's compose file is standalone. It does not show how a
    `vault` service joins the existing stack that already has `web` and
    `db`. The real question is: should Vault be a fourth service in the
    same compose file, or a separate compose stack that `web` depends on
    at runtime? The reflection never addresses this.
    Answer for this project: a separate compose file
    (`docker-compose.vault.yml`) that can be started independently. The
    `web` service gets `VAULT_ADDR`, `VAULT_ROLE_ID`, and `VAULT_SECRET_ID`
    injected at startup. This keeps the Vault lifecycle decoupled from
    the application lifecycle.

### What the reflection over-engineers for this project right now

  TLS on the Vault listener.
    TLS between containers on the same Docker bridge network is generally
    not worth the certificate management overhead for a single-developer
    project. The Docker network itself is not exposed to the internet. TLS
    becomes necessary when Vault and the application are on different hosts
    or when external audit requirements exist. For now `tls_disable = 1`
    on the internal listener is an intentional decision, not a mistake.

  Root token revocation immediately after setup.
    The reflection recommends revoking the root token once AppRole is
    configured. This is correct for a long-lived production cluster.
    For a dev setup where you may need to re-configure Vault frequently,
    revoking the root token means you need to re-initialize and re-unseal
    to recover access. Keep the root token in your password manager; revoke
    it only when the project reaches a stable production state with a
    defined break-glass procedure.

---

## Part 3 — Integration Plan for prof-horario

### What needs to change

Currently `app/config.py` reads from environment variables via
pydantic-settings. The Vault integration replaces the credential source
without changing the Settings interface that the rest of the codebase
depends on.

The target state:

  Without Vault configured (VAULT_ADDR not set):
    Settings behaves exactly as today — reads from `.env` / environment.
    Zero behaviour change for local development without Vault.

  With Vault configured (VAULT_ADDR + VAULT_ROLE_ID + VAULT_SECRET_ID set):
    Settings authenticates to Vault via AppRole at construction time,
    reads `secret/data/inovar/credentials`, and populates
    inovar_username and inovar_password from Vault.
    The `.env` credentials are ignored (or can be absent entirely).

This conditional fallback pattern means the project can adopt Vault
incrementally — the existing CI, test suite, and local dev workflow
change nothing until Vault is explicitly wired in.

### New files

  app/services/vault_client.py
    Thin wrapper around `hvac`. Handles AppRole login, token refresh,
    and KV v2 read. No FastAPI dependency — testable in isolation.

  app/config.py (updated)
    Adds optional Vault fields to Settings. The `model_post_init` hook
    calls VaultClient if VAULT_ADDR is set and overwrites the credential
    fields with values from Vault.

  vault/vault-config.hcl
    Vault server configuration for Docker Compose.

  vault/inovar-policy.hcl
    The one policy this project needs.

  docker-compose.vault.yml
    Separate compose file for the Vault service.

  tests/test_vault_client.py
    Unit tests — mock hvac, test all error paths.

  tests/test_vault_integration.py
    Integration tests — spin up Vault in dev mode, test real reads.

### Vault path structure for this project

  secret/data/inovar/credentials
    inovar_username = <value>
    inovar_password = <value>

That is the only secret this project needs from Vault right now. The
policy should be as narrow as possible:

  path "secret/data/inovar/*" {
    capabilities = ["read"]
  }

Nothing else. The `web` container must not be able to write, delete,
or list other paths.

### TDD plan

Step 0 — VaultClient unit tests (RED then GREEN)

  File: tests/test_vault_client.py
  Dependency: mock hvac.Client entirely — no Docker, no network

  Assertions to write:
    test_login_uses_approle_mount
      VaultClient("http://vault:8200", "role-id", "secret-id")
      calling .login() must call hvac.Client.auth.approle.login
      with role_id="role-id" and secret_id="secret-id"

    test_read_inovar_credentials_returns_dict
      After .login(), calling .read_inovar_credentials() must call
      hvac.Client.secrets.kv.v2.read_secret_version
      with path="inovar/credentials", mount_point="secret"
      and return {"inovar_username": ..., "inovar_password": ...}

    test_raises_vault_unavailable_when_connection_fails
      If hvac raises a ConnectionError, VaultClient must raise
      a new VaultUnavailableError (DomainError subclass, status 503)

    test_raises_vault_auth_error_when_approle_rejected
      If hvac raises Forbidden on login, VaultClient must raise
      VaultAuthError (DomainError subclass, status 401)

    test_raises_vault_secret_not_found_when_path_missing
      If hvac raises InvalidPath on read, VaultClient must raise
      VaultSecretNotFoundError (DomainError subclass, status 404)

    test_token_is_not_logged_in_repr
      The token returned by Vault must not appear in repr(client)
      (same SecretStr discipline as inovar_password)

Step 1 — Settings Vault integration unit tests (RED then GREEN)

  File: tests/test_config_vault.py
  Dependency: mock VaultClient at app.config level

  Assertions to write:
    test_settings_uses_env_when_vault_addr_absent
      When VAULT_ADDR is not set, Settings.inovar_username must
      equal INOVAR_USERNAME from the environment.
      VaultClient must never be instantiated.

    test_settings_reads_from_vault_when_vault_addr_present
      When VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID are set,
      Settings must call VaultClient.read_inovar_credentials()
      and set inovar_username and inovar_password from its return value.
      The INOVAR_USERNAME env var must be ignored.

    test_settings_raises_on_vault_unavailable
      If VaultClient raises VaultUnavailableError,
      Settings construction must re-raise it (no swallowing).
      The application must not start with empty credentials.

Step 2 — Integration tests against real dev-mode Vault (RED then GREEN)

  File: tests/test_vault_integration.py
  Dependency: Vault running in dev mode on localhost:8300
  (separate port to avoid conflicting with any production Vault)
  These tests are skipped automatically if VAULT_INTEGRATION_TEST=1
  is not set — they never run in CI unless explicitly enabled.

  Assertions to write:
    test_vault_devmode_is_reachable
      VaultClient("http://localhost:8300", root_token_as_role_id, "")
      .login() must succeed without raising.

    test_write_and_read_inovar_credentials
      After writing `inovar_username` and `inovar_password` to
      secret/data/inovar/credentials via hvac directly,
      VaultClient.read_inovar_credentials() must return the same values.

    test_policy_prevents_write_from_app_role
      A VaultClient authenticated with a token scoped to inovar-policy
      must raise VaultAuthError when attempting to write to
      secret/data/inovar/credentials.

    test_settings_reads_live_credentials_from_vault
      With real dev-mode Vault and VAULT_* env vars set,
      Settings().inovar_username must equal the value written to Vault.

### docker-compose.vault.yml

The Vault service is kept in a separate compose file to decouple its
lifecycle from the application. Start it once; restart the application
independently.

  services:
    vault:
      image: hashicorp/vault:1.15.4   # pinned, never 'latest'
      container_name: prof-vault
      ports:
        - "8200:8200"
      volumes:
        - ./vault/vault-config.hcl:/vault/config/vault-config.hcl
        - vault-data:/vault/file
      cap_add:
        - IPC_LOCK
      command: server -config=/vault/config/vault-config.hcl
      restart: unless-stopped
      networks:
        - prof-network

  The `web` service in docker-compose.yml must also join prof-network
  and receive these environment variables:
    VAULT_ADDR=http://vault:8200
    VAULT_ROLE_ID=<from CI secrets or operator>
    VAULT_SECRET_ID=<from CI secrets or operator>

  Note: VAULT_ROLE_ID and VAULT_SECRET_ID are not Vault secrets.
  The role-id is public (it identifies which role to use, not a
  credential itself). The secret-id is a one-time credential that
  Vault issues and expires. Both can live in a CI/CD secrets store
  (GitHub Actions secrets, etc.) without irony.

### vault/vault-config.hcl

  ui = true

  storage "raft" {
    path    = "/vault/file"
    node_id = "prof_vault_node"
  }

  listener "tcp" {
    address     = "0.0.0.0:8200"
    tls_disable = 1
    # tls_disable = 0 when deploying to a multi-host environment.
    # Provide tls_cert_file and tls_key_file pointing to real certs.
  }

  # For a single-developer project, mlock can be disabled to avoid
  # the need for elevated Linux capabilities on some kernel versions.
  # On a host you control with proper IPC_LOCK, set this to false.
  disable_mlock = false

### Operational notes

  Unsealing on restart.
    After `docker compose -f docker-compose.vault.yml up -d`, if this
    is a fresh start, run:

      docker exec -it prof-vault vault operator init \
        -key-shares=1 -key-threshold=1

    Save the single unseal key and root token in your password manager.
    Then:

      docker exec -it prof-vault vault operator unseal <unseal-key>

    If Vault was already initialized and just restarted, you only need
    the unseal step. The application will fail its readiness probe until
    Vault is unsealed — this is intentional and correct behaviour.

  First-time secret population.
    After unsealing:

      docker exec -it prof-vault vault login <root-token>
      docker exec -it prof-vault vault secrets enable -path=secret kv-v2
      docker exec -it prof-vault vault kv put secret/inovar/credentials \
        inovar_username="<real_value>" \
        inovar_password="<real_value>"

    Then create the policy and AppRole as documented in Part 2 of the
    reflection (those steps are correct).

  Rotating credentials.
    When the Inovar password changes:

      docker exec -it prof-vault vault kv put secret/inovar/credentials \
        inovar_username="<same>" \
        inovar_password="<new_password>"

    Restart the `web` container. Settings re-reads from Vault on boot.
    KV v2 keeps the previous version — you can roll back with:

      docker exec -it prof-vault vault kv rollback \
        -version=<N> secret/inovar/credentials

  What NOT to do.
    Do not put INOVAR_USERNAME or INOVAR_PASSWORD in docker-compose.yml
    once Vault is active. Those env vars in the compose file are the
    fallback path for when Vault is absent. Having both active at the
    same time creates confusion about which value is actually used.

---

## Dependency added by this integration

  hvac>=2.1.0    — official Python client for HashiCorp Vault

Add to requirements.txt when implementing. The library is Apache-2.0
licensed, actively maintained by HashiCorp, and has no transitive
dependencies that conflict with the existing stack.
