## 🛡️ Security Scan — ❌ Fail

**Reason:** Found medium (>= low) from semgrep at Order-app-main/public/index.html:44

### Summary
- **Total findings:** 6
- **Worst severity:** high
- 🔴 Critical: 0 | 🟠 High: 3 | 🟡 Medium: 3 | 🟢 Low: 0

### Top Recommendations

**Semgrep:**
- [MEDIUM] `Order-app-main/public/index.html:44` (HTML.SECURITY.AUDIT.MISSING-INTEGRITY.MISSING-INTEGRITY)
- [MEDIUM] `app/insecure_eval.py:2` (PYTHON.LANG.SECURITY.AUDIT.EVAL-DETECTED.EVAL-DETECTED)
- [HIGH] `creater_pr.py:17` (PYTHON.LANG.SECURITY.AUDIT.SUBPROCESS-SHELL-TRUE.SUBPROCESS-SHELL-TRUE)

---
_See `llm_recommendations_summary.md` for full details._