import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from time import perf_counter
from sqlalchemy import select, or_
from app.core.config import settings
from app.core.logging import request_id_ctx
from app.models import RagDocument, RagChunk, RagTrace
from app.rag.embeddings import embed, model_name, tokens
from app.rag.index import suspicious
from app.rag.schemas import RagAnswer, Citation
from app.rag.generation import generate
from app.rag.routing import route as choose_strategy
from app.rag.enhancements import Budget, rewrite, rerank, assess
from app.services.rollout import allowed


def keyword_scores(query, texts):
    terms = set(tokens(query))
    counts = [Counter(tokens(t)) for t in texts]
    avg = sum(sum(c.values()) for c in counts) / max(len(counts), 1) or 1
    scores = []
    for c in counts:
        score = 0.0
        for term in terms:
            n = sum(term in other for other in counts)
            frequency = c[term]
            if frequency:
                idf = math.log(1 + (len(counts)-n+0.5)/(n+0.5))
                score += idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * sum(c.values())/avg))
        scores.append(score)
    return scores


def retrieve(db, question, role, product_id=None, mode="vector", limit=None):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = list(db.execute(select(RagChunk, RagDocument).join(RagDocument).where(
        RagDocument.shop_id == settings.rag_shop_id,
        RagDocument.effective_at <= now,
        or_(RagDocument.expires_at.is_(None), RagDocument.expires_at > now),
        or_(RagDocument.product_id.is_(None), RagDocument.product_id == product_id)
    ).order_by(RagChunk.id)))
    rows = [(c, d) for c, d in rows if role.value in json.loads(d.allowed_roles)]
    if len(rows) > settings.rag_max_candidates:
        raise ValueError("candidate budget exceeded")
    if not rows:
        return []
    q = embed([question])[0] if mode != "keyword" else None
    valid = []
    for c, d in rows:
        if mode != "keyword" and c.embedding_model != model_name():
            continue  # Changing embedding model requires explicit reindexing.
        vector = json.loads(c.vector_json)
        if q is not None and len(vector) != len(q):
            continue
        valid.append((c, d, sum(x*y for x, y in zip(q, vector)) if q is not None else 0.0))
    texts = [d.title + " " + c.section + " " + c.content for c, d, _ in valid]
    bm25 = keyword_scores(question, texts)
    vrank = {i: rank for rank, i in enumerate(sorted(range(len(valid)), key=lambda i: valid[i][2], reverse=True), 1)}
    krank = {i: rank for rank, i in enumerate(sorted(range(len(valid)), key=lambda i: bm25[i], reverse=True), 1)}
    output = []
    for i, (c, d, similarity) in enumerate(valid):
        overlap = len(set(tokens(question)) & set(tokens(texts[i])))
        if (mode == "keyword" and bm25[i] <= 0) or (mode != "keyword" and similarity < settings.rag_min_similarity):
            continue
        if settings.rag_provider == "local" and not settings.rag_embedding_url and overlap < 1:
            continue
        score = bm25[i] if mode == "keyword" else similarity if mode == "vector" else 1/(60+vrank[i]) + 1/(60+krank[i])
        output.append((c, d, score))
    return sorted(output, key=lambda item: item[2], reverse=True)[:limit or settings.rag_top_k]


def answer(db, question, role, product_id=None, mode="auto"):
    from app.rag.provider import provider_calls
    calls = []
    telemetry_token = provider_calls.set(calls)
    start = perf_counter()
    ranks, citations = [], []
    reason, message = "no_evidence", "未找到足够的有效依据，请转人工客服。"
    route = "rag"
    generation_mode, usage, generation_model = "extractive", {}, None
    strategy = choose_strategy(question, product_id=product_id)
    requested_mode = mode
    mode = strategy.mode if mode == "auto" else mode
    stage_times = {}
    enhancements = Budget(start + settings.rag_total_timeout_seconds)
    enhanced = requested_mode == "auto" and settings.rag_provider == "dashscope" and strategy.name in {"single_hop", "multi_evidence"}
    retry_count, generation_retry_count = 0, 0
    rewrite_status, rerank_status = "skipped", "skipped"
    evidence_sufficient, evidence_conflict = True, False
    try:
        if not allowed(db,"rag-direct"):
            reason,message="disabled","当前Agent已停用或未在灰度范围，请转人工。"
        elif not settings.rag_enabled:
            reason = "disabled"
        elif strategy.name == "tool":
            route, reason, message = "tool", "tool_required", "此问题需要实时数据或订单资格检查，请提供订单号、商品和所需操作，通过业务工具核实。"
        elif suspicious(question):
            reason = "unsafe_instruction"
        elif strategy.name == "out_of_scope":
            reason = "no_evidence"
        elif strategy.name == "clarification":
            reason, message = "clarification", "问题涉及较多条件，请拆分为至多两个问题或转人工。"
        else:
            # At most two evidence searches; all clauses must have supporting evidence.
            parts = strategy.queries
            if len(parts) > 2:
                reason, message = "clarification", "问题涉及较多条件，请拆分为至多两个问题或转人工。"
            else:
                retrieval_start = perf_counter()
                groups = []
                for part in parts:
                    limit = settings.rag_rerank_candidates if enhanced else settings.rag_top_k
                    group = retrieve(db, part, role, product_id, mode, limit=limit)
                    if enhanced and settings.rag_rewrite_enabled:
                        try:
                            rewritten = rewrite(part, enhancements)
                            rewrite_status = "applied"
                            extra = retrieve(db, rewritten, role, product_id, mode, limit=limit)
                            # Original candidates survive rewrite failure and semantic drift.
                            fused = {}
                            for candidates in (group, extra):
                                for position, (chunk, doc, score) in enumerate(candidates, 1):
                                    old = fused.get(chunk.id, (chunk, doc, 0))
                                    fused[chunk.id] = (chunk, doc, old[2]+1/(60+position))
                            group = sorted(fused.values(), key=lambda r:r[2], reverse=True)[:limit]
                        except Exception:
                            rewrite_status = "fallback_original"
                    if enhanced and settings.rag_rerank_enabled:
                        try:
                            group = rerank(part, group, enhancements)
                            rerank_status = "applied"
                        except Exception:
                            rerank_status = "fallback_retrieval_order"
                    groups.append(group[:settings.rag_top_k])
                if enhanced and settings.rag_self_rag_enabled:
                    try:
                        verdict = assess(question, groups, enhancements)
                        evidence_sufficient, evidence_conflict = verdict.sufficient, verdict.conflict
                        if not evidence_sufficient and not evidence_conflict and verdict.missing_query:
                            retry_count = 1
                            extra = retrieve(db, verdict.missing_query, role, product_id, "hybrid")
                            if settings.rag_rerank_enabled:
                                extra = rerank(question, extra, enhancements)
                            # Supplement an empty step, otherwise preserve original per-step evidence.
                            for i, group in enumerate(groups):
                                if not group:
                                    groups[i] = extra[:settings.rag_top_k]
                            if all(groups):
                                groups.append(extra[:settings.rag_top_k])
                            verdict = assess(question, groups, enhancements)
                            evidence_sufficient, evidence_conflict = verdict.sufficient, verdict.conflict
                    except Exception:
                        evidence_sufficient = False
                stage_times["retrieval_ms"] = round((perf_counter()-retrieval_start)*1000, 3)
                ranks = [{"step": n+1, "chunk_id": c.id, "document_key": d.document_key, "section": c.section,
                          "rank": rank, "score": round(score, 6)}
                         for n, group in enumerate(groups) for rank, (c, d, score) in enumerate(group, 1)]
                if evidence_conflict:
                    reason, message = "conflict", "检索证据存在冲突，请转人工核实。"
                elif not evidence_sufficient:
                    reason, message = "insufficient_evidence", "补检索后仍无法确认足够依据，请转人工核实。"
                elif all(groups):
                    selected = list({c.id: (c, d) for group in groups for c, d, _ in group[:2]}.values())
                    claim_values = {}
                    conflict = False
                    for c, d in selected:
                        for key, value in json.loads(d.claims_json).items():
                            if key in claim_values and claim_values[key] != value:
                                conflict = True
                            claim_values[key] = value
                    if conflict:
                        reason, message = "conflict", "有效资料中的条款存在冲突，无法确定结论，请转人工核实。"
                    elif sum(len(c.content) for c, d in selected) > 8000:
                        reason = "context_budget"
                    elif any(suspicious(c.content) for c, d in selected):
                        reason = "unsafe_evidence"
                    else:
                        citations = [Citation(document_key=d.document_key, chunk_id=c.id, title=d.title,
                                              section=c.section, source_url=d.source_url, version=d.version, quote=c.content)
                                     for c, d in selected]
                        # Literal quotations are independently checked, avoiding unsupported generated conclusions.
                        message = "以下为有效资料原文，具体订单资格需通过规则引擎核实：\n" + "\n".join(
                            f"[{n}] {cite.quote}" for n, cite in enumerate(citations, 1))
                        reason = "supported"
                        if settings.rag_generation_enabled:
                            generation_model = settings.dashscope_generation_model
                            generation_mode = "dashscope"
                            generation_start = perf_counter()
                            try:
                                remaining = settings.rag_total_timeout_seconds - (perf_counter() - start)
                                if remaining <= 0:
                                    raise ValueError("budget exhausted")
                                generated, used, first_usage, reason = generate(question, citations, remaining / 2)
                                usage = first_usage
                                if not generated and enhanced and settings.rag_self_rag_enabled and reason == "generation_insufficient":
                                    generation_retry_count = 1
                                    remaining = settings.rag_total_timeout_seconds - (perf_counter()-start)
                                    if remaining <= 1:
                                        raise ValueError("budget exhausted")
                                    generated, used, next_usage, reason = generate(question + " 请仅回答证据支持的部分，不增加事实。", citations, remaining/2)
                                    for key,value in next_usage.items():
                                        usage[key] = usage.get(key,0)+value
                                if generated:
                                    message = generated
                                    # Retain numeric labels used by the generated claims.
                                else:
                                    message, citations = "生成服务无法确认充分依据，请转人工核实。", []
                            except Exception:
                                reason, message, citations = "generation_failure", "生成或证据审核暂时未通过，请转人工核实。", []
                            finally:
                                stage_times["generation_and_verification_ms"] = round((perf_counter()-generation_start)*1000, 3)
        budget = settings.rag_total_timeout_seconds if settings.rag_generation_enabled else settings.rag_timeout_seconds
        if perf_counter() - start > budget:
            reason, message, citations = "timeout", "检索超过时限，请转人工。", []
    except Exception:
        # Never expose provider errors, URLs, keys, raw questions or document contents in trace logs.
        reason, message, citations = "retrieval_failure", "检索服务暂时不可用，请转人工。", []
    elapsed = round((perf_counter() - start) * 1000, 3)
    provider_calls.reset(telemetry_token)
    trace = RagTrace(request_id=request_id_ctx.get()[:64], details_json=json.dumps({
        "route": route, "strategy": strategy.name, "strategy_reason": strategy.explanation,
        "requested_mode": requested_mode, "mode": mode, "embedding_model": model_name(), "query_rewrite": rewrite_status,
        "rerank": rerank_status, "enhancement_events": enhancements.events,
        "retrieval": ranks, "citations": [{"chunk_id": c.chunk_id, "version": c.version} for c in citations],
        "fallback": reason, "steps": max((r["step"] for r in ranks), default=0), "elapsed_ms": elapsed,
        "generation": generation_mode, "generation_model": generation_model, "token_usage": usage,
        "provider_cost": None, "provider_calls": calls, "role": role.value, "shop_id": settings.rag_shop_id
        , "stage_times": stage_times, "retrieval_retry_count": retry_count,
        "generation_retry_count": generation_retry_count, "enhancement_token_usage": enhancements.usage,
        "token_usage_scope": "generation_and_verification_only; enhancements_separate; embeddings_not_counted"
    }, ensure_ascii=False))
    db.add(trace); db.flush()
    return RagAnswer(route=route, answer=message, citations=citations, handoff=reason != "supported",
                     reason=reason, trace_id=trace.id, elapsed_ms=elapsed, generation_mode=generation_mode,
                     model=generation_model, token_usage=usage, strategy=strategy.name)
