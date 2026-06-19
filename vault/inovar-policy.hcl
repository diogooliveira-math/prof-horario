# vault/inovar-policy.hcl
#
# Least-privilege read-only policy for the inovar AppRole identity.
#
# The KV v2 engine stores secrets under the internal path:
#   secret/data/<user-path>
#
# The path pattern below covers exactly one credential set:
#   secret/data/inovar/credentials
#
# The AppRole token issued after login() has ONLY this capability.
# It cannot list, create, update, or delete — and it cannot read any
# other path in Vault.  If the scraper is compromised, the blast radius
# is limited to reading the Inovar username and password.

path "secret/data/inovar/*" {
  capabilities = ["read"]
}
