# vault/vault-config.hcl
#
# HashiCorp Vault server configuration for the prof-horario project.
#
# Storage: Raft integrated storage — single-node, no external Consul/etcd
# dependency.  Data is persisted inside the vault-data Docker volume.
#
# Listener: plain HTTP on 0.0.0.0:8200.  TLS is terminated at the reverse
# proxy (or at the Docker network boundary for local dev).  For production
# set tls_disable = 0 and supply tls_cert_file / tls_key_file.
#
# IPC_LOCK (cap_add in docker-compose.vault.yml) prevents the OS from
# swapping Vault memory pages to disk.  disable_mlock must stay false so
# Vault can actually call mlockall(2).

ui = true

storage "raft" {
  path    = "/vault/file"
  node_id = "vault_node_1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

# Windows Docker Desktop (WSL2) does not support mlockall(2) even with
# IPC_LOCK — Vault crashes on startup if this is false.  Set to true for
# local dev on Windows.  In production on a real Linux host with IPC_LOCK
# support, flip back to false so secrets cannot hit swap.
disable_mlock = true
