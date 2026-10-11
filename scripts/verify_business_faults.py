"""One invocation records six fault families, reusing regression tests where applicable."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

SCENARIOS = {
    "timeout": ["tests/test_business_faults.py::test_service_timeout"],
    "missing_order": ["tests/test_business_faults.py::test_missing_order"],
    "unauthorized_order": ["tests/test_business_faults.py::test_existing_other_user_order"],
    "duplicate_submit": ["tests/test_stage5.py::test_replay_creates_one_resource_and_audit", "tests/test_stage5.py::test_changed_request_same_key_conflicts"],
    "policy_update_delete": ["tests/test_rag.py::test_update_delete_and_repeat_import", "tests/test_rag.py::test_failed_reindex_keeps_previous_index"],
    "human_takeover": ["tests/test_stage6.py::test_takeover_is_sticky_and_metrics_not_inferred", "tests/test_consultation.py::test_out_of_scope_then_explicit_human_request"],
}


def main():
    results = {}
    Path("reports/faults").mkdir(parents=True, exist_ok=True)
    for scenario, selectors in SCENARIOS.items():
        path = Path(f"reports/faults/{scenario}.xml")
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *selectors,
                               f"--junitxml={path}"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        suites = ET.parse(path).getroot().findall(".//testsuite") if path.exists() else []
        results[scenario] = {"exit_code": proc.returncode,
                             "tests": sum(int(s.get("tests", 0)) for s in suites),
                             "failures": sum(int(s.get("failures", 0)) for s in suites),
                             "errors": sum(int(s.get("errors", 0)) for s in suites),
                             "skipped": sum(int(s.get("skipped", 0)) for s in suites), "artifact": str(path)}
        print(f"{scenario}: {'passed' if proc.returncode == 0 else 'failed'}", flush=True)
    Path("reports/business-faults.json").write_text(json.dumps({"scope": "offline fault injection, not real platform outage",
        "scenarios": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(int(any(r["exit_code"] != 0 for r in results.values())))


if __name__ == "__main__":
    main()
