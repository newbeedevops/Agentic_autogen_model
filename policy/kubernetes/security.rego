package company.kubernetes

default deny := []

is_deployment(obj) {
  obj.kind == "Deployment"
  obj.spec.template.spec
}

pod_spec(obj) = spec {
  spec := obj.spec.template.spec
}

# runAsNonRoot: allow if pod-level is set OR every container sets it
deny[msg] {
  obj := input[_]
  is_deployment(obj)
  spec := pod_spec(obj)

  not pod_or_all_containers_run_as_non_root(spec)

  msg := sprintf("Deployment/%s: must set runAsNonRoot=true (pod securityContext or per-container).",
    [obj.metadata.name])
}

pod_or_all_containers_run_as_non_root(spec) {
  spec.securityContext.runAsNonRoot == true
} else {
  # all containers have runAsNonRoot=true
  not exists_container_without_run_as_non_root(spec.containers)
}

exists_container_without_run_as_non_root(containers) {
  c := containers[_]
  not c.securityContext.runAsNonRoot
}

# Require resource limits for all containers
deny[msg] {
  obj := input[_]
  is_deployment(obj)
  spec := pod_spec(obj)

  c := spec.containers[_]
  not c.resources.limits

  msg := sprintf("Deployment/%s: container %q must define resource limits.",
    [obj.metadata.name, c.name])
}

# Disallow privileged containers
deny[msg] {
  obj := input[_]
  is_deployment(obj)
  spec := pod_spec(obj)

  c := spec.containers[_]
  c.securityContext.privileged == true

  msg := sprintf("Deployment/%s: container %q must not run privileged.",
    [obj.metadata.name, c.name])
}