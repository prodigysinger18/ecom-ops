"""Create an interview-ready report; optional offline repricing never calls models."""
import argparse
import json
import math
from pathlib import Path
from statistics import mean
from scripts.load_test_live import costs, ROOT


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", default="reports/live-load-test.json")
    p.add_argument("--before", default="reports/live-load-before-fix.json")
    p.add_argument("--output", default="reports/live-load-acceptance.md")
    p.add_argument("--embedding-rate", type=float)
    p.add_argument("--input-rate", type=float)
    p.add_argument("--output-rate", type=float)
    p.add_argument("--price-source")
    args = p.parse_args()
    rates = [args.embedding_rate, args.input_rate, args.output_rate]
    if any(v is not None for v in rates) and (not all(v is not None and math.isfinite(v) and v >= 0 for v in rates) or not args.price_source):
        p.error("provide all three rates and price-source, or none")
    r = json.loads((ROOT / args.report).read_text(encoding="utf-8"))
    if r["status"] != "completed":
        p.error("benchmark must be completed")
    rows = [row for level in r["rounds"] for row in level["rows"]]
    calls = [c for row in rows for c in row.get("provider_calls", [])]
    money = costs(calls, args)
    s = r["summary"]
    lines = ["# 真实模型完整链路性能测试", "", f"测试开始：{r['started_at']}。完成：{r['finished_at']}。",
             "", "使用真实百炼 Embedding 与通义千问；通过HTTP调用用户认证咨询接口，经过权限、敏感词检查、路由、知识库检索或业务工具、模型生成及证据审核、会话与日志保存。业务数据及知识库为模拟数据。",
             "", f"模型：`{r['config']['embedding_model']}` / `{r['config']['generation_model']}`。配置：Query Rewrite={r['config']['rewrite']}，Rerank={r['config']['rerank']}，Self-RAG={r['config']['self_rag']}。",
             "", "## 并发实测", "", "|并发客户端数|请求数|HTTP成功|业务完成|RAG完成|RAG P95|工具P95|观测吞吐|",
             "|---|---|---|---|---|---|---|---|"]
    for level in r["rounds"]:
        a = level["summary"]
        lines.append(f"|{level['workers']}|{a['requests']}|{a['http_successes']}/{a['requests']}|{a['business_successes']}/{a['requests']}|{a['rag_supported']}/{a['rag_requests']}|{a['rag_latency']['p95_ms']/1000:.2f}s|{a['tool_latency']['p95_ms']:.1f}ms|{a['observed_throughput_rps']:.2f}请求/s|")
    lines += ["", f"合计：{s['requests']}次正式请求，HTTP成功{s['http_successes']}次，业务完成{s['business_successes']}次，客户端读取超时{s['client_timeouts']}次。",
              "", "HTTP成功只代表返回200；知识库业务完成还要求真实模型生成、证据检查通过、有引用且未转入兜底。这个比例不是人工答案准确率或幻觉率。工具查询不调用模型，其耗时单独展示，避免掩盖知识问答延迟。",
              "", f"知识库未完成原因：`{json.dumps(s['rag_fallbacks'], ensure_ascii=False)}`。",
              "", "## 阶段耗时", "", "|阶段|有记录请求数|平均耗时|", "|---|---|---|"]
    for key in ("retrieval_ms", "generation_and_verification_ms"):
        values = [row["stage_times"][key] for row in rows if key in row.get("stage_times", {})]
        if values:
            lines.append(f"|{key}|{len(values)}|{mean(values):.1f}ms|")
    lines += ["", "检索阶段包括按当前配置启用的查询改写及多轮检索；生成阶段包含独立证据审核。HTTP耗时还包括数据库、服务排队及本地网络。",
              "", "## Token 与成本", "",
              f"正式请求：Embedding {money['embedding_tokens']} Token，聊天输入{money['chat_input_tokens']} Token，聊天输出{money['chat_output_tokens']} Token；共{money['provider_calls']}次供应商调用（Embedding {money['embedding_calls']}次，聊天{money['chat_calls']}次）。",
              "", f"没有返回用量的调用：{money['calls_without_reported_usage']}次。生成结果被应用拒绝时，供应商已返回的Token仍计入；数据库保存失败、网络中断或未报告用量可能使记录不完整，不能当成官方账单。",
              "", "一次性知识库向量化和预热用量单独记录，不混入正式每次咨询指标。",
              "", f"正式请求已知用量费用估算：{money['estimated_known_usage_cny']} 元。" if money['estimated_known_usage_cny'] is not None else "",]
    if money["estimated_known_usage_cny"] is None:
        lines += ["人民币费用未填写：未确认账号所属地域、模型价格档位、缓存/优惠及官方单价。请从百炼用量与账单核对；支持提供有来源的单价后离线计算，无须再调用模型。"]
    else:
        lines += [f"价格来源：{args.price_source}。按每百万Token计费，公式：Embedding Token×Embedding单价＋输入Token×输入单价＋输出Token×输出单价，最后除以1,000,000。不含未报告用量，不等于最终账单。"]
    before = ROOT / args.before
    if before.exists():
        b = json.loads(before.read_text(encoding="utf-8"))
        lines += ["", "## 发现的问题与修复", "", "初次测试中，等待模型期间的数据库写事务会阻塞并发请求。修复为先提交用户消息，再等待外部服务，缩短写锁持有时间。保留原始结果，对比同样的合成问题和并发档位；模型延迟和生成行为本身仍有波动。", "", "|并发|修复前HTTP成功|修复后HTTP成功|", "|---|---|---|"]
        for a, old in zip(r["rounds"], b["rounds"]):
            lines.append(f"|{a['workers']}|{old['summary']['http_successes']}/{old['summary']['requests']}|{a['summary']['http_successes']}/{a['summary']['requests']}|")
    lines += ["", "## 面试表述与边界", "",
              "可以说明：通过真实模型HTTP链路压测发现数据库事务持锁问题，缩短事务后用同组问题复测；分别衡量HTTP成功、业务完成、RAG与工具延迟，并覆盖Embedding、改写、生成及证据审核的供应商Token记录。",
              "", f"不可说明：已经达到生产SLA、支持长期稳定并发、具有某个答案准确率或幻觉率。这是小样本闭环负载测试，每档仅{r['config']['requests_per_level']}条请求且重复相同6种场景，P95容易受单次请求影响；不是持续高压容量测试，没有覆盖真实平台、正式登录和跨机器网络。",
              "", "复跑：`python -m scripts.load_test_live --workers 1 3 5 --requests 12`（付费模型调用）。",
              "", "只重算费用：`python -m scripts.analyze_live_load --embedding-rate <单价> --input-rate <单价> --output-rate <单价> --price-source <官方价格与日期>`（不调用模型）。", ""]
    (ROOT / args.output).write_text("\n".join(lines), encoding="utf-8")
    (ROOT / args.output).with_suffix(".cost.json").write_text(json.dumps(money, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
