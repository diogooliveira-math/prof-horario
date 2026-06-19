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

# Set to true only if the host kernel forbids mlock (some CI environments).
# Never set to true in production — it allows secrets to hit swap.
disable_mlock = false
