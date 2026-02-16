package company.terraform

default deny := []

# Helpers
rc := input.resource_changes

is_create_or_update(change) {
  change.change.actions[_] == "create"  # or update
} else {
  change.change.actions[_] == "update"
}

after(change) = obj {
  obj := change.change.after
  obj != null
}

deny[msg] {
  some i
  r := rc[i]
  is_create_or_update(r)
  r.type == "aws_s3_bucket_public_access_block"
  a := after(r)

  # Require all public access blocks enabled
  not (a.block_public_acls && a.block_public_policy && a.ignore_public_acls && a.restrict_public_buckets)

  msg := sprintf("Terraform: %s.%s must enable all S3 public access block settings.",
    [r.type, r.name])
}

deny[msg] {
  some i
  r := rc[i]
  is_create_or_update(r)
  r.type == "aws_security_group_rule"
  a := after(r)

  a.type == "ingress"
  cidr := a.cidr_blocks[_]
  cidr == "0.0.0.0/0"

  # “Sensitive” ports example: SSH/RDP
  a.from_port <= 22
  a.to_port >= 22

  msg := sprintf("Terraform: %s.%s allows 0.0.0.0/0 to SSH (port 22).",
    [r.type, r.name])
}

deny[msg] {
  some i
  r := rc[i]
  is_create_or_update(r)
  r.type == "aws_ebs_volume"
  a := after(r)

  not a.encrypted
  msg := sprintf("Terraform: %s.%s must have encrypted=true.", [r.type, r.name])
}