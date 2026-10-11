"""Compute answer metrics only for human labels bound to this exact run/answer."""
import argparse
import json
from pathlib import Path
from app.rag.metrics import reviewed_metrics
from scripts.benchmark_interview import digest


def apply_reviews(report, reviews):
    answers = {(p, row["id"]): row for p, item in report["profiles"].items() for row in item.get("rows", [])}
    seen = set()
    for review in reviews:
        key = (review["profile"], review["id"])
        if key in seen or key not in answers:
            raise ValueError("duplicate or unknown reviewed case")
        seen.add(key)
        if review["run_id"] != report["run_id"] or review["answer_sha256"] != digest(answers[key]["answer"]):
            raise ValueError("review does not belong to this run/answer")
        if review.get("reviewed") is not True:
            continue
        if not review.get("reviewer") or type(review.get("answer_correct")) is not bool or type(review.get("has_hallucination")) is not bool:
            raise ValueError("approved review requires reviewer and boolean labels")
        total, supported = review.get("claims_total"), review.get("claims_supported")
        if type(total) is not int or type(supported) is not int or not 0 <= supported <= total:
            raise ValueError("invalid claim counts")
    return {profile: reviewed_metrics([r for r in reviews if r["profile"] == profile])
            for profile in report["profiles"] if report["profiles"][profile].get("rows")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="reports/interview-benchmark.json")
    parser.add_argument("--reviews", default="reports/interview-benchmark.reviews.json")
    parser.add_argument("--output", default="reports/interview-reviewed.json")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    reviews = json.loads(Path(args.reviews).read_text(encoding="utf-8"))
    result = {"run_id": report["run_id"], "metrics": apply_reviews(report, reviews)}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
