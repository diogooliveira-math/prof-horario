"""
tests/test_infra_files.py

RED phase — infrastructure file existence and consistency tests.

These tests verify that every file required to run the service with Vault
actually exists on disk and contains the minimum content needed to work
correctly. They are NOT about Vault being running — they are about the
repository being in a complete, reproducible state.

WHY test files in pytest?
  Configuration files are code. They are wrong just as often as code is.
  A docker-compose.yml that references a network that the vault compose
  file does not declare is a bug — it just happens to be in YAML instead
  of Python. Testing it here means `pytest` catches it before
  `docker compose up` fails with a cryptic networking error at 11pm.

What each test validates:

  test_vault_config_hcl_exists
    vault/vault-config.hcl must exist and declare raft storage and a tcp
    listener. Without these Vault cannot start.

  test_vault_policy_hcl_exists_and_path_matches_vault_client
    vault/inovar-policy.hcl must exist and its path pattern must match
    exactly what VaultClient.read_inovar_credentials() reads.
    This is a cross-reference test — if VaultClient changes its path
    and the policy is not updated, this test goes RED.

  test_docker_compose_vault_exists
    docker-compose.vault.yml must exist and declare a vault service.

  test_docker_compose_vault_declares_external_network
    The vault compose file must join the external network so the web
    container can reach vault by hostname.

  test_docker_compose_app_declares_same_external_network
    The app docker-compose.yml must join the same named external network.
    If one side declares it and the other does not, vault:8200 is
    unreachable from the web container.

  test_network_name_matches_between_compose_files
    The external network name must be identical in both compose files.
    This is the most common cause of silent "can't resolve vault" failures.

  test_bootstrap_script_exists_and_is_valid_bash
    vault/bootstrap.sh must exist and pass bash -n (syntax check).
    A bootstrap script with a typo is worse than no bootstrap script —
    it runs, fails silently, and leaves Vault in a half-configured state.

  test_env_example_exists_with_all_required_keys
    .env.example must exist and list all keys a developer needs to set.
    This is the onboarding document for anyone setting up the project.

  test_env_example_lists_vault_keys
    Specifically, the Vault keys must be present — not just the legacy
    INOVAR_USERNAME/INOVAR_PASSWORD keys that predate Vault.

  test_dot_env_is_gitignored
    The actual .env file must be gitignored. Committing real credentials
    to git history is permanent even after force-pushing.

CURRENT STATE: all tests are RED because none of these files exist.
"""
from __future__ import annotations

import subprocess
import yaml
import os
import re

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(rel_path: str) -> str:
    full = os.path.join(ROOT, rel_path)
    if not os.path.exists(full):
        pytest.fail(f"Required file does not exist: {rel_path}")
    with open(full) as f:
        return f.read()


def _read_yaml(rel_path: str) -> dict:
    content = _read(rel_path)
    return yaml.safe_load(content)


# ===========================================================================
# vault/vault-config.hcl
# ===========================================================================

def test_vault_config_hcl_exists():
    """vault/vault-config.hcl must exist."""
    path = os.path.join(ROOT, "vault", "vault-config.hcl")
    assert os.path.exists(path), (
        "vault/vault-config.hcl is missing. "
        "Create it with raft storage and tcp listener config."
    )


def test_vault_config_hcl_declares_raft_storage():
    content = _read("vault/vault-config.hcl")
    assert "raft" in content, (
        "vault-config.hcl does not declare raft storage. "
        "Add:  storage \"raft\" { path = \"/vault/file\" ... }"
    )


def test_vault_config_hcl_declares_tcp_listener():
    content = _read("vault/vault-config.hcl")
    assert "listener" in content and "tcp" in content, (
        "vault-config.hcl does not declare a tcp listener. "
        "Add:  listener \"tcp\" { address = \"0.0.0.0:8200\" ... }"
    )


# ===========================================================================
# vault/inovar-policy.hcl
# ===========================================================================

def test_vault_policy_hcl_exists():
    """vault/inovar-policy.hcl must exist."""
    path = os.path.join(ROOT, "vault", "inovar-policy.hcl")
    assert os.path.exists(path), (
        "vault/inovar-policy.hcl is missing. "
        "Create it with a read-only policy on secret/data/inovar/*."
    )


def test_vault_policy_hcl_grants_read_on_inovar_path():
    """The policy must grant read access on the inovar credentials path."""
    content = _read("vault/inovar-policy.hcl")
    assert "inovar" in content, (
        "inovar-policy.hcl does not reference the inovar path."
    )
    assert "read" in content, (
        "inovar-policy.hcl does not grant read capability."
    )


def test_vault_policy_path_matches_vault_client_read_path():
    """
    The path in inovar-policy.hcl must be consistent with what
    VaultClient.read_inovar_credentials() actually reads.

    VaultClient reads:  path="inovar/credentials", mount_point="secret"
    which resolves to:  secret/data/inovar/credentials  (KV v2 prefix)

    The policy must cover this path — either exactly or via a wildcard
    like secret/data/inovar/* .
    """
    content = _read("vault/inovar-policy.hcl")

    # Accept either the exact path or a wildcard that covers it
    has_exact = "secret/data/inovar/credentials" in content
    has_wildcard = "secret/data/inovar/*" in content

    assert has_exact or has_wildcard, (
        "inovar-policy.hcl does not cover secret/data/inovar/credentials. "
        "VaultClient reads from that exact path. "
        "Add:  path \"secret/data/inovar/*\" { capabilities = [\"read\"] }"
    )


# ===========================================================================
# docker-compose.vault.yml
# ===========================================================================

def test_docker_compose_vault_yml_exists():
    path = os.path.join(ROOT, "docker-compose.vault.yml")
    assert os.path.exists(path), (
        "docker-compose.vault.yml is missing. "
        "Create it with a vault service using hashicorp/vault image."
    )


def test_docker_compose_vault_declares_vault_service():
    compose = _read_yaml("docker-compose.vault.yml")
    services = compose.get("services", {})
    assert "vault" in services, (
        "docker-compose.vault.yml has no 'vault' service. "
        "Add a service named 'vault' using hashicorp/vault image."
    )


def test_docker_compose_vault_uses_pinned_image_not_latest():
    compose = _read_yaml("docker-compose.vault.yml")
    image = compose["services"]["vault"].get("image", "")
    assert "hashicorp/vault" in image, (
        f"vault service image '{image}' does not look like a HashiCorp Vault image."
    )
    assert "latest" not in image, (
        "vault service uses 'latest' tag. Pin to an explicit version like "
        "hashicorp/vault:1.15.4 so deployments are reproducible."
    )


def test_docker_compose_vault_exposes_port_8200():
    compose = _read_yaml("docker-compose.vault.yml")
    ports = compose["services"]["vault"].get("ports", [])
    port_strings = [str(p) for p in ports]
    assert any("8200" in p for p in port_strings), (
        "vault service does not expose port 8200. "
        "Add  ports: - '8200:8200'  to the vault service."
    )


def test_docker_compose_vault_mounts_config_file():
    compose = _read_yaml("docker-compose.vault.yml")
    volumes = compose["services"]["vault"].get("volumes", [])
    vol_strings = [str(v) for v in volumes]
    assert any("vault-config.hcl" in v for v in vol_strings), (
        "vault service does not mount vault-config.hcl. "
        "Add the config file as a volume: ./vault/vault-config.hcl:/vault/config/..."
    )


def test_docker_compose_vault_has_ipc_lock_capability():
    compose = _read_yaml("docker-compose.vault.yml")
    caps = compose["services"]["vault"].get("cap_add", [])
    assert "IPC_LOCK" in caps, (
        "vault service is missing cap_add: IPC_LOCK. "
        "Without it, the OS can swap Vault memory pages to disk, "
        "potentially writing raw secrets to the swap partition."
    )


def test_docker_compose_vault_declares_external_network():
    compose = _read_yaml("docker-compose.vault.yml")
    networks = compose.get("networks", {})
    external_nets = [
        name for name, cfg in networks.items()
        if isinstance(cfg, dict) and cfg.get("external") is True
    ]
    assert len(external_nets) >= 1, (
        "docker-compose.vault.yml declares no external network. "
        "The web container cannot reach vault:8200 unless both stacks "
        "share an external named Docker network. "
        "Add:  networks:\\n  prof-net:\\n    external: true"
    )


def test_vault_service_joins_external_network():
    compose = _read_yaml("docker-compose.vault.yml")
    service_networks = compose["services"]["vault"].get("networks", [])
    top_networks = compose.get("networks", {})
    external_nets = {
        name for name, cfg in top_networks.items()
        if isinstance(cfg, dict) and cfg.get("external") is True
    }

    if isinstance(service_networks, dict):
        joined = set(service_networks.keys())
    else:
        joined = set(service_networks)

    overlap = joined & external_nets
    assert overlap, (
        "The vault service does not join any external network. "
        f"External networks declared: {external_nets}. "
        f"Networks joined by vault service: {joined}. "
        "Add the external network name under  services.vault.networks."
    )


# ===========================================================================
# docker-compose.yml — must join the same network as vault
# ===========================================================================

def test_docker_compose_app_declares_external_network():
    compose = _read_yaml("docker-compose.yml")
    networks = compose.get("networks", {})
    external_nets = [
        name for name, cfg in networks.items()
        if isinstance(cfg, dict) and cfg.get("external") is True
    ]
    assert len(external_nets) >= 1, (
        "docker-compose.yml declares no external network. "
        "The web container cannot reach vault:8200 unless both stacks "
        "share an external named Docker network."
    )


def test_web_service_joins_external_network():
    compose = _read_yaml("docker-compose.yml")
    service_networks = compose["services"]["web"].get("networks", [])
    top_networks = compose.get("networks", {})
    external_nets = {
        name for name, cfg in top_networks.items()
        if isinstance(cfg, dict) and cfg.get("external") is True
    }

    if isinstance(service_networks, dict):
        joined = set(service_networks.keys())
    else:
        joined = set(service_networks)

    overlap = joined & external_nets
    assert overlap, (
        "The web service does not join the external network. "
        f"External networks declared: {external_nets}. "
        f"Networks joined by web: {joined}."
    )


def test_network_name_matches_between_both_compose_files():
    """
    The external network name must be identical in docker-compose.yml and
    docker-compose.vault.yml. A one-character typo means vault:8200 is
    silently unreachable — no error at compose-up time, only a 503 at runtime.
    """
    def _external_network_names(rel_path):
        compose = _read_yaml(rel_path)
        return {
            name for name, cfg in compose.get("networks", {}).items()
            if isinstance(cfg, dict) and cfg.get("external") is True
        }

    app_nets = _external_network_names("docker-compose.yml")
    vault_nets = _external_network_names("docker-compose.vault.yml")

    shared = app_nets & vault_nets
    assert shared, (
        f"No external network name is shared between the two compose files.\n"
        f"  docker-compose.yml external networks:       {app_nets}\n"
        f"  docker-compose.vault.yml external networks: {vault_nets}\n"
        "The network name must be identical — Docker resolves container "
        "hostnames only within the same network."
    )


# ===========================================================================
# vault/bootstrap.sh
# ===========================================================================

def test_bootstrap_script_exists():
    path = os.path.join(ROOT, "vault", "bootstrap.sh")
    assert os.path.exists(path), (
        "vault/bootstrap.sh is missing. "
        "Create a script that automates post-init Vault configuration: "
        "enable KV v2, write policy, enable AppRole, create role, "
        "and print the role-id and secret-id."
    )


def test_bootstrap_script_has_valid_bash_syntax():
    path = os.path.join(ROOT, "vault", "bootstrap.sh")
    if not os.path.exists(path):
        pytest.skip("bootstrap.sh does not exist yet")

    result = subprocess.run(
        ["bash", "-n", path],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"vault/bootstrap.sh has invalid bash syntax:\n{result.stderr}"
    )


def test_bootstrap_script_enables_kv_v2():
    content = _read("vault/bootstrap.sh")
    assert "kv" in content.lower() or "secrets enable" in content, (
        "bootstrap.sh does not enable the KV secrets engine. "
        "Add: vault secrets enable -path=secret kv-v2"
    )


def test_bootstrap_script_creates_approle():
    content = _read("vault/bootstrap.sh")
    assert "approle" in content, (
        "bootstrap.sh does not set up AppRole auth. "
        "Add: vault auth enable approle  and  vault write auth/approle/role/..."
    )


def test_bootstrap_script_applies_policy():
    content = _read("vault/bootstrap.sh")
    assert "policy" in content, (
        "bootstrap.sh does not write the inovar policy. "
        "Add: vault policy write inovar-policy ..."
    )


# ===========================================================================
# .env.example
# ===========================================================================

def test_env_example_exists():
    path = os.path.join(ROOT, ".env.example")
    assert os.path.exists(path), (
        ".env.example is missing. "
        "Create it listing all environment variables the service needs, "
        "with placeholder values (never real credentials)."
    )


def test_env_example_lists_vault_keys():
    content = _read(".env.example")
    for key in ("VAULT_ADDR", "VAULT_ROLE_ID", "VAULT_SECRET_ID"):
        assert key in content, (
            f".env.example is missing {key}. "
            "A new developer following this file would not know to set it."
        )


def test_env_example_lists_fallback_inovar_keys():
    """
    The fallback env keys must remain in .env.example so developers can
    still run without Vault by setting these directly.
    """
    content = _read(".env.example")
    for key in ("INOVAR_USERNAME", "INOVAR_PASSWORD", "INOVAR_URL"):
        assert key in content, (
            f".env.example is missing {key}. "
            "Keep these as documented fallback for Vault-free local dev."
        )


def test_env_example_does_not_contain_real_credentials():
    """
    .env.example must use placeholder values — never real passwords.
    This test uses heuristics: short placeholders with angle brackets or
    'your_', 'change_me', 'xxx' patterns are expected.
    A value that looks like a real URL with a known domain is a failure.
    """
    content = _read(".env.example")
    assert "inovarmais.com" not in content, (
        ".env.example contains a real Inovar URL. "
        "Use a placeholder like INOVAR_URL=https://your-inovar-instance/login"
    )


# ===========================================================================
# .gitignore — .env must be ignored
# ===========================================================================

def test_dot_env_is_gitignored():
    gitignore = _read(".gitignore")
    lines = [l.strip() for l in gitignore.splitlines()]
    # Accept '.env', '*.env', or '.env*' patterns
    is_ignored = any(
        re.fullmatch(r"\.env\*?", line) or line == "*.env"
        for line in lines
    )
    assert is_ignored, (
        ".env is not in .gitignore. "
        "Add '.env' to .gitignore before committing any credentials."
    )
