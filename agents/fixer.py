# agents/fixer.py
"""
Fixer (Patch-first, Target-scoped)

Key fixes vs previous version:
- Strict targeting: only patch files present in agent_output/targets.json (written in analysis phase)
- LLM no longer returns diffs (which were corrupt in logs). Instead it returns full updated file content.
  We then generate a git-apply friendly diff ourselves via git diff --no-index.
- Patch validation: we run `git apply --check` on patches before writing them.
- Deterministic patches only run on targeted files (no surprise edits).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import re
import os
import json
import subprocess
import tempfile

try:
    import yaml
except Exception:
    yaml = None

# Robust imports: work whether llm_bridge are in agents/ or repo root
try:
    from agents.llm_bridge import assistant_factory
except Exception:
    try:
        from llm_bridge import assistant_factory  # type: ignore
    except Exception:
        assistant_factory = None


def _safe_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", path)


def _strip_diff_preamble(diff: str) -> str:
    """Prefer patches starting at first '--- ' header."""
    if not diff:
        return diff
    m = re.search(r"(?m)^---\s+", diff)
    return diff[m.start():] if m else diff


def _make_unified_diff_git(old_text: str, new_text: str, repo_rel_path: str, context: int = 3) -> str:
    """Generate robust unified diff via git's engine."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        a = td_path / "a.txt"
        b = td_path / "b.txt"
        a.write_text(old_text or "", encoding="utf-8", newline="\n")
        b.write_text(new_text or "", encoding="utf-8", newline="\n")

        cmd = [
            "git", "diff", "--no-index", f"--unified={context}", "--no-color",
            f"--label=a/{repo_rel_path}", f"--label=b/{repo_rel_path}",
            str(a), str(b),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        diff = _strip_diff_preamble(res.stdout or "")
        if not diff.strip():
            return ""
        if not diff.endswith("\n"):
            diff += "\n"
        return diff


def _git_apply_check(patch_text: str) -> bool:
    """Validate a patch is apply-able (syntactically) with git apply --check."""
    try:
        p = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", "-"],
            input=patch_text,
            text=True,
            capture_output=True,
        )
        return p.returncode == 0
    except Exception:
        return False


class Fixer:
    def __init__(self, config: Dict[str, Any], output_dir: Path, repo_root: Optional[Path] = None):
        self.cfg = config or {}
        self.out = Path(output_dir)
        self.repo = Path(repo_root) if repo_root else Path(".")
        self.patch_dir = self.out / "patches"
        self.patch_dir.mkdir(parents=True, exist_ok=True)

        defaults = (self.cfg.get("remediation") or {}).get("defaults", {})
        self.docker_nonroot_user = defaults.get("docker_nonroot_user", "appuser")
        self.k8s_default_cpu_limit = defaults.get("k8s_default_cpu_limit", "250m")
        self.k8s_default_mem_limit = defaults.get("k8s_default_mem_limit", "256Mi")
        self.terraform_allowed_cidr = defaults.get("terraform_allowed_cidr", "10.0.0.0/24")

        self.targets = self._load_targets()

    # -------------------------
    # Targeting
    # -------------------------

    def _load_targets(self) -> List[str]:
        """
        Load canonical allowlist of repo-relative paths from agent_output/targets.json.
        If missing, we fall back to "no targets" (so Fixer will do nothing, by design).
        """
        p = self.out / "targets.json"
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            targets = data.get("targets") or []
            # Normalize to repo-relative POSIX paths
            out = []
            for t in targets:
                if not isinstance(t, str):
                    continue
                t2 = t.replace("\\", "/").lstrip("/")
                if t2 and t2 not in out:
                    out.append(t2)
            return out
        except Exception:
            return []

    def _is_target(self, repo_rel_path: str) -> bool:
        repo_rel_path = repo_rel_path.replace("\\", "/").lstrip("/")
        return repo_rel_path in set(self.targets)

    def _resolve_repo_rel(self, reported_path: str) -> Optional[str]:
        """
        Resolve a finding's 'file' to a canonical repo-relative path, but ONLY if it is in targets.
        We do not guess beyond targets.json to avoid ambiguity.
        """
        if not reported_path:
            return None
        rp = reported_path.replace("\\", "/").lstrip("/")
        if self._is_target(rp):
            return rp
        return None

    # -------------------------
    # Public API
    # -------------------------

    def apply(self, findings_grouped: Any) -> Dict[str, Any]:
        notes: List[str] = []
        emitted: List[str] = []

        if not self.targets:
            notes.append("No targets.json found (or empty). Skipping remediation by design.")
            return {
                "changed": False,
                "files": [],
                "notes": notes,
                "llm_report": None,
                "llm_report_path": None,
            }

        notes.append(f"Target allowlist loaded: {len(self.targets)} file(s)")

        # 1) Deterministic quick patches (safe, local transforms) for targeted semgrep files
        qp_notes, qp_emitted = self._apply_semgrep_quick_patches(findings_grouped)
        notes.extend(qp_notes)
        emitted.extend(qp_emitted)

        # 2) Deterministic infra patches, but ONLY if those files are targeted
        det_notes, det_emitted = self._apply_deterministic_patches()
        notes.extend(det_notes)
        emitted.extend(det_emitted)

        # 3) Optional LLM content-based patches (safe: we generate diff ourselves)
        if os.getenv("LLM_AUTOFIX", "").strip() == "1":
            llm_notes, llm_emitted = self._apply_llm_content_patches(findings_grouped)
            notes.extend(llm_notes)
            emitted.extend(llm_emitted)

        # Write audit
        self.out.mkdir(parents=True, exist_ok=True)
        try:
            (self.out / "remediation_changes.txt").write_text(
                "PATCHES EMITTED:\n" +
                "\n".join(emitted if emitted else ["(none)"]) +
                "\n\nNOTES:\n" + "\n".join(notes if notes else ["(none)"]),
                encoding="utf-8",
            )
        except Exception as e:
            notes.append(f"Error writing remediation_changes.txt: {e}")

        return {
            "changed": bool(emitted),
            "files": emitted,
            "notes": notes,
            "llm_report": None,
            "llm_report_path": None,
        }

    # -------------------------
    # Findings normalization
    # -------------------------

    def _semgrep_items(self, findings_grouped: Any) -> List[Dict[str, Any]]:
        if isinstance(findings_grouped, dict):
            v = findings_grouped.get("semgrep")
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        return []

    # -------------------------
    # Patch writing
    # -------------------------

    def _write_patch(self, repo_rel_path: str, patch_text: str, prefix: str) -> str:
        text = (patch_text or "").lstrip("\ufeff\r\n")
        text = _strip_diff_preamble(text)

        if not (text.startswith("--- ") and "\n+++ " in text and "\n@@ " in text):
            return ""

        # Validate with git apply --check to avoid "corrupt patch" PRs
        if not _git_apply_check(text):
            return ""

        out_path = self.patch_dir / f"{prefix}{_safe_name(repo_rel_path)}.patch"
        out_path.write_text(text, encoding="utf-8", newline="\n")
        return str(out_path)

    # -------------------------
    # Semgrep quick patches (targeted)
    # -------------------------

    def _apply_semgrep_quick_patches(self, findings_grouped: Any) -> Tuple[List[str], List[str]]:
        notes: List[str] = []
        emitted: List[str] = []

        for it in self._semgrep_items(findings_grouped):
            repo_rel = self._resolve_repo_rel(it.get("file") or "")
            if not repo_rel:
                continue

            fpath = self.repo / repo_rel
            if not fpath.exists() or not fpath.is_file():
                notes.append(f"Target file missing on disk: {repo_rel}")
                continue

            original = fpath.read_text(encoding="utf-8", errors="ignore")
            rule = (it.get("rule_id") or "").lower()
            msg = (it.get("message") or "").lower()

            # HTML missing integrity (SRI placeholder)
            if repo_rel.lower().endswith((".html", ".htm")) and ("missing-integrity" in rule or "integrity" in msg):
                new_text, changed = self._patch_html_add_sri(original)
                if changed:
                    diff = _make_unified_diff_git(original, new_text, repo_rel, context=3)
                    p = self._write_patch(repo_rel, diff, prefix="det_html_")
                    if p:
                        emitted.append(p)
                        notes.append(f"HTML SRI placeholder added: {repo_rel}")
                continue

            # Python eval -> ast.literal_eval
            if repo_rel.lower().endswith(".py") and ("eval" in msg or "eval" in rule):
                new_text, changed = self._patch_python_eval(original)
                if changed:
                    diff = _make_unified_diff_git(original, new_text, repo_rel, context=3)
                    p = self._write_patch(repo_rel, diff, prefix="det_eval_")
                    if p:
                        emitted.append(p)
                        notes.append(f"eval() replaced with ast.literal_eval(): {repo_rel}")
                continue

            # subprocess shell=True -> shell=False (+ import shlex)
            if repo_rel.lower().endswith(".py") and ("subprocess" in msg or "shell" in msg or "subprocess" in rule or "shell" in rule):
                new_text, changed = self._patch_python_subprocess_shell(original)
                if changed:
                    diff = _make_unified_diff_git(original, new_text, repo_rel, context=3)
                    p = self._write_patch(repo_rel, diff, prefix="det_subproc_")
                    if p:
                        emitted.append(p)
                        notes.append(f"subprocess shell hardened: {repo_rel}")
                continue

        return notes, emitted

    def _patch_html_add_sri(self, text: str) -> Tuple[str, bool]:
        changed = False

        def repl(m):
            nonlocal changed
            tag = m.group(0)
            if re.search(r'\bintegrity\s*=\s*["\']', tag, flags=re.I):
                return tag
            changed = True
            return re.sub(r'>\s*$', ' integrity="sha384-TODO-SRI-HASH" crossorigin="anonymous">', tag)

        new_text = re.sub(r'<script\b[^>]*>', repl, text, flags=re.I)
        return new_text, changed

    def _patch_python_eval(self, text: str) -> Tuple[str, bool]:
        new_text = re.sub(r'\beval\s*\(', 'ast.literal_eval(', text)
        changed = new_text != text
        if changed and not re.search(r'^\s*import\s+ast\b', new_text, flags=re.M):
            new_text = "import ast\n" + new_text
        return new_text, changed

    def _patch_python_subprocess_shell(self, text: str) -> Tuple[str, bool]:
        new_text = re.sub(r'shell\s*=\s*True', 'shell=False', text)
        changed = new_text != text
        if changed and not re.search(r'^\s*import\s+shlex\b', new_text, flags=re.M):
            new_text = "import shlex\n" + new_text
        return new_text, changed

    # -------------------------
    # Deterministic infra patches (ONLY if targeted)
    # -------------------------

    def _apply_deterministic_patches(self) -> Tuple[List[str], List[str]]:
        notes: List[str] = []
        emitted: List[str] = []

        # Dockerfile (only if targeted)
        docker_rel = "app/Dockerfile"
        if self._is_target(docker_rel):
            p = self.repo / docker_rel
            if p.exists():
                orig = p.read_text(encoding="utf-8", errors="ignore")
                new = self._fix_dockerfile_text(orig, notes)
                if new != orig:
                    diff = _make_unified_diff_git(orig, new, docker_rel, context=3)
                    outp = self._write_patch(docker_rel, diff, prefix="det_docker_")
                    if outp:
                        emitted.append(outp)

        # K8s YAMLs (only those in targets)
        if yaml:
            for rel in self.targets:
                if not rel.startswith("k8s/") or not rel.endswith((".yml", ".yaml")):
                    continue
                p = self.repo / rel
                if not p.exists():
                    continue
                orig = p.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
                try:
                    docs = list(yaml.safe_load_all(orig))
                except Exception:
                    continue

                changed_any = False
                fixed_docs = []
                for d in docs:
                    if not isinstance(d, dict):
                        fixed_docs.append(d)
                        continue
                    fixed, ch = self._fix_k8s_obj(d)
                    fixed_docs.append(fixed)
                    changed_any = changed_any or ch

                if changed_any:
                    new = yaml.safe_dump_all(fixed_docs, sort_keys=False)
                    diff = _make_unified_diff_git(orig, new, rel, context=3)
                    outp = self._write_patch(rel, diff, prefix="det_k8s_")
                    if outp:
                        emitted.append(outp)
                        notes.append(f"K8s hardened: {rel}")

        # Terraform (only those in targets)
        for rel in self.targets:
            if not rel.startswith("terraform/") or not rel.endswith(".tf"):
                continue
            p = self.repo / rel
            if not p.exists():
                continue
            orig = p.read_text(encoding="utf-8", errors="ignore")
            new = self._fix_tf_text(orig, notes)
            if new != orig:
                diff = _make_unified_diff_git(orig, new, rel, context=3)
                outp = self._write_patch(rel, diff, prefix="det_tf_")
                if outp:
                    emitted.append(outp)

        return notes, emitted

    def _fix_dockerfile_text(self, txt: str, notes: List[str]) -> str:
        out = re.sub(
            r"(?im)^\s*ADD\s+([^\s]+)\s+([^\s]+)\s*$",
            lambda m: (notes.append(f"Dockerfile: ADD→COPY {m.group(1)} -> {m.group(2)}") or f"COPY {m.group(1)} {m.group(2)}"),
            txt,
        )
        has_user = re.search(r"(?im)^\s*USER\s+.+$", out) is not None
        is_root_user = re.search(r"(?im)^\s*USER\s+root\s*$", out) is not None
        if (not has_user) or is_root_user:
            user = self.docker_nonroot_user
            block = (
                "\n# Security: create non-root user and switch\n"
                f"RUN (adduser --disabled-password --gecos '' {user}) || (adduser -D {user}) || (useradd -m {user} || true)\n"
                f"USER {user}\n"
            )
            m = re.search(r"(?im)^(\s*)(CMD|ENTRYPOINT)\b", out)
            out = out[:m.start()] + block + out[m.start():] if m else (out.rstrip() + "\n" + block)
            notes.append(f"Dockerfile: ensured USER {user} (non-root)")
        return out

    def _fix_k8s_obj(self, obj: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        changed = False
        kind = (obj.get("kind") or "").strip()

        def harden_container(c: Dict[str, Any]) -> bool:
            nonlocal changed
            sc2 = c.setdefault("securityContext", {})
            if sc2.get("privileged") is True:
                sc2["privileged"] = False
                changed = True
            if sc2.get("allowPrivilegeEscalation") is not False:
                sc2["allowPrivilegeEscalation"] = False
                changed = True
            res = c.setdefault("resources", {})
            lim = res.setdefault("limits", {})
            if not lim:
                lim["cpu"] = self.k8s_default_cpu_limit
                lim["memory"] = self.k8s_default_mem_limit
                changed = True
            return True

        if kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"}:
            tpl = obj.setdefault("spec", {}).setdefault("template", {}).setdefault("spec", {})
            sc = tpl.setdefault("securityContext", {})
            if sc.get("runAsNonRoot") is not True:
                sc["runAsNonRoot"] = True
                changed = True
            for c in (tpl.get("containers") or []):
                if isinstance(c, dict):
                    harden_container(c)

        if kind == "Pod":
            spec = obj.setdefault("spec", {})
            sc = spec.setdefault("securityContext", {})
            if sc.get("runAsNonRoot") is not True:
                sc["runAsNonRoot"] = True
                changed = True
            for c in (spec.get("containers") or []):
                if isinstance(c, dict):
                    harden_container(c)

        return obj, changed

    def _fix_tf_text(self, text: str, notes: List[str]) -> str:
        n = re.sub(
            r'(?i)cidr_blocks\s*=\s*\[\s*"0\.0\.0\.0/0"\s*\]',
            f'cidr_blocks = ["{self.terraform_allowed_cidr}"]',
            text,
        )
        n2 = re.sub(
            r'(?i)ipv6_cidr_blocks\s*=\s*\[\s*"::/0"\s*\]',
            f'ipv6_cidr_blocks = ["{self.terraform_allowed_cidr}"]',
            n,
        )
        if n2 != text:
            notes.append("Terraform: replaced world-open CIDR with allowed CIDR")
        return n2

    # -------------------------
    # LLM content patches (safe)
    # -------------------------

    def _apply_llm_content_patches(self, findings_grouped: Any) -> Tuple[List[str], List[str]]:
        notes: List[str] = []
        emitted: List[str] = []

        if assistant_factory is None:
            notes.append("LLM_AUTOFIX=1 but assistant_factory not available; skipping LLM patches.")
            return notes, emitted

        # Only patch targeted semgrep files for now (you can extend later)
        for it in self._semgrep_items(findings_grouped):
            repo_rel = self._resolve_repo_rel(it.get("file") or "")
            if not repo_rel:
                continue

            fpath = self.repo / repo_rel
            if not fpath.exists():
                continue

            original = fpath.read_text(encoding="utf-8", errors="ignore")
            rule = it.get("rule_id", "")
            msg = it.get("message", "")

            agent = assistant_factory(
                name="content_fixer",
                system_message=(
                    "You are a senior secure code reviewer.\n"
                    "Return ONLY the full updated file content.\n"
                    "Do not return a diff. Do not use code fences. Do not add commentary.\n"
                    "Keep changes minimal and directly related to the issue.\n"
                ),
                temperature=float(os.getenv("OLLAMA_TEMP", "0.1") or "0.1"),
            )

            user_prompt = (
                f"File path: {repo_rel}\n"
                f"Semgrep rule_id: {rule}\n"
                f"Finding message: {msg}\n\n"
                "Current file content:\n"
                "-----BEGIN FILE-----\n"
                f"{original}\n"
                "-----END FILE-----\n"
            )

            try:
                new_text = agent.chat_completion_fn(
                    [{"role": "system", "content": agent.system_message},
                     {"role": "user", "content": user_prompt}]
                ) or ""
            except Exception as e:
                notes.append(f"LLM failed for {repo_rel}: {e}")
                continue

            new_text = new_text.replace("\r\n", "\n").strip("\ufeff")
            if not new_text or new_text.strip() == original.strip():
                notes.append(f"LLM produced no change for {repo_rel}")
                continue

            diff = _make_unified_diff_git(original, new_text + ("\n" if not new_text.endswith("\n") else ""), repo_rel, context=3)
            p = self._write_patch(repo_rel, diff, prefix="llm_content_")
            if p:
                emitted.append(p)
                notes.append(f"LLM content patch emitted: {repo_rel}")
            else:
                notes.append(f"LLM content patch rejected by validation (git apply --check) for {repo_rel}")

        return notes, emitted