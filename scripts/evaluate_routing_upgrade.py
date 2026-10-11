import hashlib
import json
from pathlib import Path
from collections import Counter
from app.schemas import ChatRequest
from app.services.consultation import check_sensitive, consultation_route


def evaluate(path):
    cases = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        actual = "safety_block" if check_sensitive(case["question"])["blocked"] else consultation_route(
            ChatRequest(user_external_id="validation", message=case["question"]))[0]
        rows.append({"id": case["id"], "question": case["question"], "expected": case["expected_route"],
                     "actual": actual, "correct": actual == case["expected_route"]})
    return {"dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "count": len(rows),
            "correct": sum(r["correct"] for r in rows), "accuracy": sum(r["correct"] for r in rows)/len(rows),
            "confusion": dict(Counter(f"{r['expected']} -> {r['actual']}" for r in rows)), "rows": rows}


def main():
    path = Path("data/evaluation/routing-validation.json")
    manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("frozen validation set changed")
    dev = Path("data/evaluation/holdout.json")
    old = json.loads(Path("reports/interview-benchmark.json").read_text(encoding="utf-8"))
    result = {"scope": "synthetic unreviewed candidate labels, not production accuracy",
              "development_before": {"correct": old["routes"]["correct"], "count": old["routes"]["count"]},
              "development_after": evaluate(dev), "new_validation": evaluate(path)}
    if {r["question"] for r in result["development_after"]["rows"]} & {r["question"] for r in result["new_validation"]["rows"]}:
        raise ValueError("exact duplicate development and validation queries")
    Path("reports/routing-upgrade.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: {f: v for f, v in item.items() if f not in {"rows", "confusion"}}
                      for k, item in result.items() if isinstance(item, dict)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
