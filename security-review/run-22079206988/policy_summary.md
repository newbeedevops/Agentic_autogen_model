# Policy Gate Summary

**Status:** ❌ FAIL
**Reason:** Found medium (>= low) from semgrep at Order-app-main/public/index.html:44

## Stats
- Total: 4
- Worst severity: high
- Weighted score: 9

### By Severity
  - 🔴 critical: 0
  - 🟠 high: 1
  - 🟡 medium: 3
  - 🟢 low: 0

### By Category
  - code: 4
  - infra: 0
  - image: 0
  - policy: 0
  - secrets: 0
  - webapp: 0

### By Tool
  - semgrep: 4

## Violations
1. 🟡 **[MEDIUM]** semgrep @ `Order-app-main/public/index.html:44` (id: `HTML.SECURITY.AUDIT.MISSING-INTEGRITY.MISSING-INTEGRITY`)
   - This tag is missing an 'integrity' subresource integrity attribute. The 'integrity' attribute allows for the browser to verify that externally hosted files (for example from a CDN) are delivered witho
2. 🟡 **[MEDIUM]** semgrep @ `app/insecure_eval.py:2` (id: `PYTHON.LANG.SECURITY.AUDIT.EVAL-DETECTED.EVAL-DETECTED`)
   - Detected the use of eval(). eval() can be dangerous if used to evaluate dynamic content. If this content can be input from outside the program, this may be a code injection vulnerability. Ensure evalu
3. 🟠 **[HIGH]** semgrep @ `creater_pr.py:17` (id: `PYTHON.LANG.SECURITY.AUDIT.SUBPROCESS-SHELL-TRUE.SUBPROCESS-SHELL-TRUE`)
   - Found 'subprocess' function 'check_call' with 'shell=True'. This is dangerous because this call will spawn the command using a shell process. Doing so propagates current shell settings and variables, 
4. 🟡 **[MEDIUM]** semgrep @ `k8s/deployment.yaml:18` (id: `YAML.KUBERNETES.SECURITY.ALLOW-PRIVILEGE-ESCALATION.ALLOW-PRIVILEGE-ESCALATION`)
   - In Kubernetes, each pod runs in its own isolated environment with its own set of security policies. However, certain container images may contain `setuid` or `setgid` binaries that could allow an atta
