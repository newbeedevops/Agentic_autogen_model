package company.docker

default deny := []

# Disallow ADD
deny[msg] {
  some i
  lower(input[i].Cmd) == "add"
  msg := sprintf("Dockerfile: use COPY instead of ADD (line %v).", [i+1])
}

# Disallow FROM :latest
deny[msg] {
  some i
  lower(input[i].Cmd) == "from"
  image := lower(concat(" ", input[i].Value))
  contains(image, ":latest")
  msg := sprintf("Dockerfile: avoid :latest in FROM (%v). Pin a version or digest.", [image])
}

# Require USER and require it to be non-root
deny[msg] {
  not user_specified
  msg := "Dockerfile: no USER specified. Set a non-root USER."
}

deny[msg] {
  user_specified
  not user_non_root
  msg := "Dockerfile: USER is root. Set a non-root USER."
}

user_specified {
  some i
  lower(input[i].Cmd) == "user"
  u := trim(concat(" ", input[i].Value))
  u != ""
}

user_non_root {
  some i
  lower(input[i].Cmd) == "user"
  u := lower(trim(concat(" ", input[i].Value)))
  u != ""
  u != "root"
}

# apt-get hygiene (basic)
deny[msg] {
  some i
  lower(input[i].Cmd) == "run"
  run := lower(concat(" ", input[i].Value))
  contains(run, "apt-get install")
  not contains(run, "rm -rf /var/lib/apt/lists")
  msg := "Dockerfile: apt-get install without cleaning apt lists (add: rm -rf /var/lib/apt/lists/*)."
}