import json
from collections import Counter
from scripts.seed_rag import ROOT
from app.rag.routing import route


def main():
    cases=json.loads((ROOT/"data/rag/routing-test.json").read_text(encoding="utf-8"))
    rows=[]
    confusion=Counter()
    for case in cases:
        predicted=route(case["question"],order_no=case.get("order_no")).name
        confusion[(case["strategy"],predicted)]+=1
        rows.append({"id":case["id"],"expected":case["strategy"],"predicted":predicted,"correct":predicted==case["strategy"]})
    labels=sorted({c["strategy"] for c in cases})
    report={"dataset":"synthetic-routing-25", "cases":len(rows), "accuracy":sum(r["correct"] for r in rows)/len(rows),
            "confusion_matrix":{expected:{predicted:confusion[(expected,predicted)] for predicted in labels} for expected in labels},
            "rows":rows,"limitations":"合成小样本，规则路由非模型意图识别；不代表真实业务准确率。保留未覆盖同义表达错误。"}
    target=ROOT/"reports/routing-v2.json";target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in {"rows","confusion_matrix"}},ensure_ascii=True))


if __name__=="__main__":main()
