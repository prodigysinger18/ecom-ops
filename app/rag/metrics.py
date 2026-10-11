"""Retrieval metrics from relevance labels, answer metrics only from reviewed labels."""
from math import ceil


def retrieval_metrics(ranked_ids, relevant_ids, k=5):
    if k <= 0:
        raise ValueError("k must be positive")
    ranked = list(dict.fromkeys(ranked_ids))[:k]
    relevant = set(relevant_ids)
    if not relevant:
        return {"precision_at_k": None, "recall_at_k": None, "mrr_at_k": None}
    hits = sum(item in relevant for item in ranked)
    return {"precision_at_k": hits/k, "recall_at_k": hits/len(relevant),
            "mrr_at_k": next((1/(i+1) for i, item in enumerate(ranked) if item in relevant), 0.0)}


def reviewed_metrics(labels):
    """No labels means unavailable, never automatic 100% faithfulness."""
    approved = [r for r in labels if r.get("reviewer") and r.get("reviewed") is True]
    def average(field):
        values = [r[field] for r in approved if isinstance(r.get(field), bool)]
        return sum(values)/len(values) if values else None
    total = sum(r.get("claims_total", 0) for r in approved)
    supported = sum(r.get("claims_supported", 0) for r in approved)
    if total < supported or min(total, supported) < 0:
        raise ValueError("invalid claim annotations")
    return {"faithfulness": supported/total if total else None,
            "answer_accuracy": average("answer_correct"),
            "hallucination_rate": average("has_hallucination"),
            "reviewed_cases": len(approved)}


def percentile(values, p=0.95):
    return sorted(values)[max(0, ceil(len(values)*p)-1)] if values else None
