package dockerfile.security

deny contains msg if {
  input[i].Cmd == "add"
  msg := sprintf("Dockerfile uses ADD at instruction %d; use COPY instead", [i])
}

deny contains msg if {
  some i
  lower(input[i].Cmd) == "user"
  lower(input[i].Value[0]) == "root"
  msg := sprintf("Dockerfile sets USER root at instruction %d; use a non-root user", [i])
}

deny contains msg if {
  some i
  lower(input[i].Cmd) == "from"
  endswith(lower(input[i].Value[0]), ":latest")
  msg := sprintf("Dockerfile uses :latest tag at instruction %d; pin to a specific version", [i])
}