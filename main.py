# main.py
import argparse
import os
import json
import sys
from pathlib import Path
from datetime import datetime
from io import StringIO
import contextlib

try:
    import yaml
except Exception:
    yaml = None

# LLM imports
try:
    from agents.llm_bridge import assistant_factory, check_ollama_health, get_fallback_suggestion
except Exception:
    assistant_factory = None
    check_ollama_health = None
    get_fallback_suggestion = None

# Core agent imports
try:
    from agents.collector import CollectorAgent
    from agents.policy_gate import PolicyGate
    from agents.reporter import Reporter
    from agents.fixer import Fixer
    from agents.autogen_runtime import run_autogen_layer
except Exception:
    from collector import CollectorAgent
    from policy_gate import PolicyGate
    from reporter import Reporter
    from fixer import Fixer
    from autogen_runtime import run_autogen_layer


def load_env_from_file(env_file: str = ".env", override: bool = False) -> None:
    p = Path(env_file)
    if not p.exists():
        return
    try:
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if override or key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


@contextlib.contextmanager
def suppress_verbose_output():
    if os.getenv("LLM_VERBOSE", "0") == "1":
        yield
    else:
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            yield
        finally:
            sys.stdout = old_stdout


def _load_cfg() -> dict:
    default_cfg = {
        "inputs": {"reports_dir": "reports", "output_dir": "agent_output"},
        "policy": {
            "remediation": {"auto_pr": True},
            "min_severity_to_fail": os.getenv("MIN_SEVERITY", "high"),
        },
        "llm": {
            "enabled": bool(os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST")) or (os.getenv("LLM_ENABLED", "").strip() == "1"),
            "model": os.getenv("LLM_MODEL", "qwen2.5-coder:3b"),
            "temperature": float(os.getenv("OLLAMA_TEMPERATURE", os.getenv("OLLAMA_TEMP", "0.2"))),
        },
        "remediation": {
            "defaults": {
                "docker_nonroot_user": "appuser",
                "k8s_default_cpu_limit": "250m",
                "k8s_default_mem_limit": "256Mi",
                "terraform_allowed_cidr": "10.0.0.0/24",
            }
        },
        "write_output": True,
    }

    cfg_path = Path("config/settings.yaml")
    if yaml and cfg_path.exists():
        try:
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            for k, v in loaded.items():
                if isinstance(v, dict) and isinstance(default_cfg.get(k), dict):
                    default_cfg[k].update(v)
                else:
                    default_cfg[k] = v
        except Exception:
            pass

    return default_cfg


def print_banner():
    if not os.getenv("OLLAMA_URL") and os.getenv("OLLAMA_HOST"):
        os.environ["OLLAMA_URL"] = f"http://{os.getenv('OLLAMA_HOST')}"
    print("\n" + "=" * 70)
    print("   🛡️  DevSecOps Agentic AI Security Scanner")
    print("=" * 70)
    print(f"   📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   🤖 LLM: {os.getenv('LLM_MODEL', 'qwen2.5-coder:3b')} @ {os.getenv('OLLAMA_URL', 'localhost:11434')}")
    print("=" * 70 + "\n")


def _canonicalize_path(repo: Path, reported: str) -> str:
    """
    Convert a finding path into a repo-relative canonical path.
    We only do minimal resolution:
      - if reported exists as-is relative to repo -> use it
      - else if APP_DIR/reported exists -> use that
    """
    if not reported:
        return ""
    rp = reported.replace("\\", "/").lstrip("/")
    p1 = repo / rp
    if p1.exists() and p1.is_file():
        return rp

    app_dir = os.getenv("APP_DIR", "").strip()
    if app_dir:
        p2 = repo / app_dir / rp
        if p2.exists() and p2.is_file():
            return f"{app_dir}/{rp}".replace("\\", "/")

    return rp  # last resort (still recorded, but may not exist)


def _build_targets_json(repo: Path, findings_grouped: dict) -> List[str]:
    targets: List[str] = []
    if not isinstance(findings_grouped, dict):
        return targets

    def add(path: str):
        p = path.replace("\\", "/").lstrip("/")
        if p and p not in targets:
            targets.append(p)

    # semgrep
    for it in (findings_grouped.get("semgrep") or []):
        if not isinstance(it, dict):
            continue
        f = _canonicalize_path(repo, it.get("file") or "")
        if f:
            add(f)

    # gitleaks/tfsec/trivy can be added later if you want to patch them too
    # for now, keep scope tight to avoid accidental edits.

    return targets


def main():
    load_env_from_file(".env", override=False)

    parser = argparse.ArgumentParser(description="DevSecOps Agentic AI Pipeline")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--skip-llm", action="store_true")

    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--analysis-only", action="store_true")
    phase.add_argument("--generate-fixes", action="store_true")

    parser.add_argument("--model", default=os.getenv("LLM_MODEL"))
    parser.add_argument("--ollama-url", default=None)

    args = parser.parse_args()

    if args.verbose:
        os.environ["LLM_VERBOSE"] = "1"
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    if args.ollama_url:
        os.environ["OLLAMA_URL"] = args.ollama_url

    print_banner()

    cfg = _load_cfg()
    if args.model:
        cfg.setdefault("llm", {})["model"] = args.model

    reports_dir = Path(cfg["inputs"]["reports_dir"])
    output_dir = Path(cfg["inputs"]["output_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    repo = Path(".")

    # 1) Collect
    print("   ⏳ Scanning...")
    try:
        with suppress_verbose_output():
            collector = CollectorAgent(cfg, reports_dir, output_dir)
            findings_grouped = collector.load_all()
    except Exception as e:
        print(f"   ❌ Collector error: {e}")
        findings_grouped = {}

    # write merged findings
    try:
        (output_dir / "merged_findings.json").write_text(
            json.dumps({"findings": findings_grouped}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    # 1b) Write canonical targets.json (the “contract” between analysis and fix jobs)
    targets = _build_targets_json(repo, findings_grouped)
    try:
        (output_dir / "targets.json").write_text(
            json.dumps({"targets": targets}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    # 2) Policy gate
    print("   ⏳ Evaluating policy...")
    try:
        with suppress_verbose_output():
            decision = PolicyGate(cfg, output_dir).decide(findings_grouped)
    except Exception as e:
        decision = {"status": "ok", "reason": f"PolicyGate error: {e}"}

    decision.setdefault("open_pr", bool(decision.get("status") == "fail"))
    decision.setdefault("remediation", {})
    decision["remediation"]["targets_path"] = str(output_dir / "targets.json")
    decision["remediation"]["targets_count"] = len(targets)

    # 3) LLM analysis (optional)
    llm_report = None
    if not args.skip_llm:
        print("   ⏳ Analyzing...")
        try:
            with suppress_verbose_output():
                llm_report = run_autogen_layer(findings_grouped, cfg, output_dir)
            if llm_report:
                decision["remediation"]["llm_report"] = llm_report
                decision["remediation"]["llm_report_path"] = str(output_dir / "llm_report.json")
        except Exception:
            pass

    # 4) Fixer (only in --generate-fixes, only when fail)
    if args.generate_fixes and decision.get("status") == "fail":
        print("[main] Fixer condition met: --generate-fixes and status=fail")
        try:
            with suppress_verbose_output():
                fix_info = Fixer(cfg, output_dir).apply(findings_grouped)
            if isinstance(fix_info, dict):
                decision["remediation"].update({k: v for k, v in fix_info.items() if k})
        except Exception as e:
            print(f"[main] Fixer error: {e}")
    else:
        print(f"[main] Skipping Fixer — args.generate_fixes={args.generate_fixes} status={decision.get('status')}")

    # 5) Reporting
    try:
        with suppress_verbose_output():
            Reporter(cfg, output_dir).emit(findings_grouped, decision)
    except Exception:
        pass

    # 6) Write decision.json
    try:
        (output_dir / "decision.json").write_text(json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return 1 if decision.get("status") == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())