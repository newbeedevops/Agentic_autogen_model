package terraform.security

# -----------------------------
# Helpers
# -----------------------------

# All resources in the plan (both create/update)
resources[r] {
  r := input.resource_changes[_]
  r.type != ""
}

# Returns true if this change contains "cidr_blocks" with 0.0.0.0/0
has_world_open_ipv4(rc) {
  after := rc.change.after
  after.cidr_blocks[_] == "0.0.0.0/0"
}

# Returns true if this change contains "ipv6_cidr_blocks" with ::/0
has_world_open_ipv6(rc) {
  after := rc.change.after
  after.ipv6_cidr_blocks[_] == "::/0"
}

# Generic “has encryption disabled” signal (commonly used keys)
encryption_disabled(rc) {
  after := rc.change.after
  after.encrypted == false
} or {
  after := rc.change.after
  after.storage_encrypted == false
}

# -----------------------------
# Policies
# -----------------------------

# 1) Block world-open ingress/egress CIDRs (AWS SG rules commonly)
deny contains msg if {
  rc := resources[_]
  rc.mode == "managed"
  rc.type == "aws_security_group_rule"
  has_world_open_ipv4(rc)
  msg := sprintf("World-open IPv4 CIDR found in %s.%s (%s). Replace 0.0.0.0/0 with a restricted CIDR.", [rc.type, rc.name, rc.address])
}

deny contains msg if {
  rc := resources[_]
  rc.mode == "managed"
  rc.type == "aws_security_group_rule"
  has_world_open_ipv6(rc)
  msg := sprintf("World-open IPv6 CIDR found in %s.%s (%s). Replace ::/0 with a restricted CIDR.", [rc.type, rc.name, rc.address])
}

# 2) S3 public access block should be enabled
deny contains msg if {
  rc := resources[_]
  rc.type == "aws_s3_bucket_public_access_block"
  after := rc.change.after
  after.block_public_acls != true
  msg := sprintf("S3 public access block is not enabled (block_public_acls) for %s (%s).", [rc.name, rc.address])
}

deny contains msg if {
  rc := resources[_]
  rc.type == "aws_s3_bucket_public_access_block"
  after := rc.change.after
  after.block_public_policy != true
  msg := sprintf("S3 public access block is not enabled (block_public_policy) for %s (%s).", [rc.name, rc.address])
}

deny contains msg if {
  rc := resources[_]
  rc.type == "aws_s3_bucket_public_access_block"
  after := rc.change.after
  after.ignore_public_acls != true
  msg := sprintf("S3 public access block is not enabled (ignore_public_acls) for %s (%s).", [rc.name, rc.address])
}

deny contains msg if {
  rc := resources[_]
  rc.type == "aws_s3_bucket_public_access_block"
  after := rc.change.after
  after.restrict_public_buckets != true
  msg := sprintf("S3 public access block is not enabled (restrict_public_buckets) for %s (%s).", [rc.name, rc.address])
}

# 3) Encryption must be enabled when a resource has encryption keys
deny contains msg if {
  rc := resources[_]
  rc.type == "aws_ebs_volume"
  encryption_disabled(rc)
  msg := sprintf("EBS volume encryption disabled for %s (%s). Set encrypted=true.", [rc.name, rc.address])
}

deny contains msg if {
  rc := resources[_]
  rc.type == "aws_db_instance"
  encryption_disabled(rc)
  msg := sprintf("RDS storage encryption disabled for %s (%s). Set storage_encrypted=true.", [rc.name, rc.address])
}

# 4) Simple “no hard-coded AWS keys” in variables (plan JSON sometimes includes these)
deny contains msg if {
  v := input.variables[_]
  lower(v.name) == "aws_access_key"
  msg := "Potential hard-coded AWS access key detected in variables. Use environment variables or secrets manager."
}