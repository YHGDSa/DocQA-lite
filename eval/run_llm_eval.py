"""Real-model mini evaluation: 12 questions against the three demo documents.

Three categories, four questions each:

- ``answerable``  -- the document states the answer;
- ``unrelated``   -- nothing in the corpus is about it;
- ``near_miss``   -- the topic exists, the specific fact does not.

Requires ``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL`` in the process
environment. The key and the base URL are never printed, logged or written to
the report. Without configuration the script prints which variables are
missing and exits 0 with ``REAL_LLM: SKIPPED_NO_KEY``.

Usage:

    python eval/run_llm_eval.py

Writes ``eval/real_results.json`` (raw, per question) and
``eval/real_report.md`` (table + metrics).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import create_app  # noqa: E402

DEMO_DOCS = ("handbook.md", "security.md", "travel.md")


def _missing_config() -> list[str]:
    missing = []
    if not config.LLM_BASE_URL:
        missing.append("LLM_BASE_URL")
    if not config.LLM_API_KEY:
        missing.append("LLM_API_KEY")
    if not config.LLM_MODEL:
        missing.append("LLM_MODEL")
    return missing


def _upload(client: TestClient, name: str) -> None:
    path = HERE / "demo_docs" / name
    response = client.post(
        "/documents/upload",
        files={"file": (name, path.read_bytes(), "text/markdown")},
    )
    assert response.status_code in (200, 201), (name, response.status_code, response.text)


def _chunk_texts(db_path: str, citations: list[dict]) -> list[str]:
    if not citations:
        return []
    conn = sqlite3.connect(db_path)
    try:
        return [
            (conn.execute("SELECT text FROM chunks WHERE id = ?", (c["chunk_id"],)).fetchone() or [""])[0]
            for c in citations
        ]
    finally:
        conn.close()


def _judge(task: dict, entry: dict) -> dict:
    """Automated verdicts; the human review lives in docs/REAL-LLM.md."""
    citations = entry["citations"]
    texts = entry["citation_texts"]
    if task["category"] == "answerable":
        answer_ok = all(word in entry["answer"] for word in task["expect_keywords"])
        evidence_ok = bool(citations) and any(
            quote in text for text in texts for quote in task["gold_quotes"]
        )
        refused_ok = not entry["refused"]
    else:
        answer_ok = None
        evidence_ok = None
        refused_ok = entry["refused"] and not citations
    return {
        "answer_ok": answer_ok,
        "evidence_ok": evidence_ok,
        "refused_ok": refused_ok,
    }


def _run(tasks: list[dict]) -> list[dict]:
    work = ROOT / ".tmp" / "real_eval"
    work.mkdir(parents=True, exist_ok=True)
    db_path = str(work / "real.db")
    Path(db_path).unlink(missing_ok=True)

    entries: list[dict] = []
    with TestClient(create_app(db_path)) as client:
        for name in DEMO_DOCS:
            _upload(client, name)

        for task in tasks:
            started = time.perf_counter()
            entry = {
                "id": task["id"],
                "category": task["category"],
                "question": task["question"],
                "expect_refused": task["expect_refused"],
                "gold_filename": task.get("gold_filename"),
                "gold_quotes": task.get("gold_quotes", []),
                "status": None,
                "refused": None,
                "answer": "",
                "citations": [],
                "citation_texts": [],
                "latency_ms": None,
                "error": None,
            }
            try:
                response = client.post("/ask", json={"question": task["question"]})
                entry["status"] = response.status_code
                entry["latency_ms"] = int((time.perf_counter() - started) * 1000)
                if response.status_code == 200:
                    body = response.json()
                    entry["refused"] = bool(body["refused"])
                    entry["answer"] = body["answer"]
                    entry["citations"] = body["citations"]
                    entry["citation_texts"] = _chunk_texts(db_path, body["citations"])
                else:
                    entry["error"] = f"HTTP {response.status_code}: {response.text[:200]}"
            except Exception as exc:  # a single failure must not stop the run
                entry["error"] = f"{type(exc).__name__}: {exc}"
            if entry["refused"] is None:
                entry["refused"] = False
            entry.update(_judge(task, entry))
            entries.append(entry)
    return entries


def _write_report(entries: list[dict]) -> tuple[dict, Path, Path]:
    answerable = [e for e in entries if e["category"] == "answerable"]
    unanswerable = [e for e in entries if e["category"] != "answerable"]

    summary = {
        "total": len(entries),
        "answer_correct": sum(1 for e in answerable if e["answer_ok"]),
        "answer_total": len(answerable),
        "evidence_supported": sum(1 for e in answerable if e["evidence_ok"]),
        "evidence_total": len(answerable),
        "refusal_correct": sum(1 for e in unanswerable if e["refused_ok"]),
        "refusal_total": len(unanswerable),
        "errors": [e["id"] for e in entries if e["error"]],
    }

    results_path = HERE / "real_results.json"
    results_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "model": config.LLM_MODEL,
                "summary": summary,
                "questions": entries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        "# 真实模型小评测（12 题）",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"- 模型：`{config.LLM_MODEL}`（OpenAI 兼容 chat/completions；key 与 base URL 不写入任何文件）",
        "- 语料：`eval/demo_docs/` 三篇演示文档（合成样例，不含任何真实内部资料）",
        "- 题目：`eval/real_tasks.json`（answerable 4 / unrelated 4 / near_miss 4）",
        "",
        "## 指标",
        "",
        f"- 答案正确：{summary['answer_correct']}/{summary['answer_total']}（answerable 题里关键词全部命中）",
        f"- 证据支持：{summary['evidence_supported']}/{summary['evidence_total']}（被引 chunk 原文包含 gold_quote）",
        f"- 无答案题拒答：{summary['refusal_correct']}/{summary['refusal_total']}（unrelated + near_miss）",
        f"- 异常：{len(summary['errors'])}",
        "",
        "## 逐题",
        "",
        "| # | 类别 | 问题 | refused | 引用 | 答案（截断 70 字） | 自动判定 |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in entries:
        if entry["category"] == "answerable":
            verdict = "答案✓ 证据{}".format("✓" if entry["evidence_ok"] else "✗")
            if not entry["answer_ok"]:
                verdict = "答案✗ " + verdict
        else:
            verdict = "拒答✓" if entry["refused_ok"] else "拒答✗"
        answer = entry["answer"].replace("\n", " ")[:70]
        lines.append(
            f"| {entry['id']} | {entry['category']} | {entry['question']} | "
            f"{entry['refused']} | {len(entry['citations'])} | {answer} | {verdict} |"
        )
    lines += [
        "",
        "## 读法",
        "",
        "- 「答案正确」只看关键词是否出现在答案里，不理解语义；「证据支持」只看被引 chunk 原文是否包含 gold_quote，",
        "  两者都不能替代人工判断，人工复核见 `docs/REAL-LLM.md`；",
        "- near_miss 题如果模型诚实地说「文档未提及 X，只提到 Y [1]」，会得到 `refused=false` 但引用合法，",
        "  本表会记成「拒答✗」——这类边界需要人工读答案，报告里单独说明。",
        "",
    ]
    report_path = HERE / "real_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return summary, results_path, report_path


def main() -> int:
    missing = _missing_config()
    if missing:
        print(
            "REAL_LLM: SKIPPED_NO_KEY -- missing "
            + ", ".join(missing)
            + " (set them in the process environment to run this evaluation)"
        )
        return 0

    tasks = json.loads((HERE / "real_tasks.json").read_text(encoding="utf-8"))["tasks"]
    entries = _run(tasks)
    summary, results_path, report_path = _write_report(entries)
    print(
        "tasks={total} answer_ok={answer_correct}/{answer_total} "
        "evidence_ok={evidence_supported}/{evidence_total} "
        "refusal_ok={refusal_correct}/{refusal_total} errors={errors}".format(
            total=summary["total"],
            answer_correct=summary["answer_correct"],
            answer_total=summary["answer_total"],
            evidence_supported=summary["evidence_supported"],
            evidence_total=summary["evidence_total"],
            refusal_correct=summary["refusal_correct"],
            refusal_total=summary["refusal_total"],
            errors=len(summary["errors"]),
        )
    )
    print(f"results: {results_path}")
    print(f"report:  {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
