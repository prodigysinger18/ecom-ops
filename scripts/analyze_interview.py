"""Analyze saved reports without incurring another API call."""
import argparse
import json
from pathlib import Path


def reported_chat_cost(rows, input_price, output_price):
    if input_price is None or output_price is None:
        return None
    if input_price < 0 or output_price < 0:
        raise ValueError("prices must be nonnegative")
    usages = [row.get(field, {}) for row in rows for field in ("generation_usage", "enhancement_usage")]
    return sum(u.get("prompt_tokens", 0)*input_price + u.get("completion_tokens", 0)*output_price
               for u in usages)/1_000_000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="reports/interview-benchmark.json")
    parser.add_argument("--input-price", type=float, help="Your applicable currency price per million input tokens")
    parser.add_argument("--output-price", type=float, help="Your applicable currency price per million output tokens")
    args = parser.parse_args()
    if (args.input_price is None) != (args.output_price is None):
        parser.error("both prices must be supplied together")
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    comparison = {}
    baseline = report["profiles"]["vector"]["summary"]
    numeric_keys = ("document_recall_at_5", "document_precision_at_5", "document_mrr_at_5", "p95_pipeline_ms",
                    "reported_generation_tokens", "reported_enhancement_tokens")
    for name, item in report["profiles"].items():
        if item["status"] != "completed":
            comparison[name] = {"status": item["status"]}
            continue
        summary = item["summary"]
        comparison[name] = {"summary": summary,
                            "delta_from_vector": {key: summary[key]-baseline[key] for key in numeric_keys
                                                  if summary.get(key) is not None and baseline.get(key) is not None},
                            "reported_chat_cost_estimate": reported_chat_cost(item["rows"], args.input_price, args.output_price)}
    result = {"run_id": report["run_id"], "comparison": comparison,
              "cost_scope": "reported chat usage only; excludes embeddings, indexing, failed calls with missing usage, infrastructure; not an invoice",
              "input_price_per_million": args.input_price, "output_price_per_million": args.output_price,
              "failure_cases": [r for r in report["routes"]["rows"] if not r["correct"]]}
    output = Path(args.report).with_suffix(".analysis.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {output}; routing failures: {len(result['failure_cases'])}")


if __name__ == "__main__":
    main()
