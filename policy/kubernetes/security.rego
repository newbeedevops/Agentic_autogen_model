package kubernetes.security

# Conftest passes Kubernetes YAML docs as input
deny contains msg if {
  is_workload
  not has_run_as_non_root
  msg := "Kubernetes workload must set spec.template.spec.securityContext.runAsNonRoot=true"
}

deny contains msg if {
  is_workload
  some c
  container := input.spec.template.spec.containers[c]
  not container.securityContext.allowPrivilegeEscalation == false
  msg := sprintf("Container %q must set securityContext.allowPrivilegeEscalation=false", [container.name])
}

deny contains msg if {
  is_workload
  some c
  container := input.spec.template.spec.containers[c]
  not container.resources.limits
  msg := sprintf("Container %q should define resource limits", [container.name])
}

is_workload if {
  kinds := {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"}
  kinds[input.kind]
}

has_run_as_non_root if {
  input.spec.template.spec.securityContext.runAsNonRoot == true
}