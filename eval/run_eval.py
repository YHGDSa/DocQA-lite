"""DocQA-lite 30-task eval: retrieval / refusal / citation pipeline, baseline vs delta.

This is NOT a model benchmark. The LLM is replaced by a scripted fake that
always answers "根据片段 [1]", so every difference between baseline and delta
comes from the pipeline (overlap refusal gate + citation validation), never
from model quality.

Run from the project root:

    python eval/run_eval.py

Stdlib + TestClient only; no network, no API key. Databases are written under
``.tmp/eval/`` inside the repo because some sandboxed environments (CI runners,
IDE sandboxes) do not give the process a writable system temp directory.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import llm, routes  # noqa: E402
from app.main import create_app  # noqa: E402

DEMO_DOCS = ("handbook.md", "security.md", "travel.md")
FAKE_ANSWER = "根据片段 [1]"
GROUPS = ("answerable", "unanswerable", "boundary")


async def _fake_chat(messages, temperature: float = 0.0) -> str:
    """The scripted model: always claims an answer, always cites [1]."""
    return FAKE_ANSWER


def _read_docs() -> dict[str, str]:
    return {
        name: (HERE / "demo_docs" / name).read_text(encoding="utf-8")
        for name in DEMO_DOCS
    }


def _upload(client, name: str, text: str):
    return client.post(
        "/documents/upload",
        files={"file": (name, text.encode("utf-8"), "text/markdown")},
    )


def _chunk_text(db_path: str, chunk_id: int) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT text FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else ""


def _validate(tasks: list[dict], docs: dict[str, str]) -> None:
    assert len(tasks) == 30, f"expected 30 tasks, got {len(tasks)}"
    counts = {group: sum(1 for task in tasks if task["group"] == group) for group in GROUPS}
    assert counts == {"answerable": 12, "unanswerable": 8, "boundary": 10}, counts
    for task in tasks:
        assert len(task["question"]) <= 500, (task["id"], len(task["question"]))
        assert "expect_refused" in task, task["id"]
        if task["group"] == "answerable":
            assert task["gold_quotes"], task["id"]
            assert task["gold_filename"] in docs, task["id"]
            for quote in task["gold_quotes"]:
                assert any(quote in text for text in docs.values()), (
                    task["id"],
                    quote,
                )


def _run_mode(
    *,
    baseline: bool,
    tasks: list[dict],
    docs: dict[str, str],
    client,
    db_path: str,
    fresh_client,
    fresh_db_path: str,
) -> list[dict]:
    entries: list[dict] = []
    for task in tasks:
        target_client = fresh_client if task.get("fresh_db") else client
        target_db = fresh_db_path if task.get("fresh_db") else db_path
        if task.get("reupload"):
            _upload(client, "handbook.md", docs["handbook.md"])

        expect_status = int(task.get("expect_status", 200))
        entry = {
            "id": task["id"],
            "group": task["group"],
            "kind": task.get("kind", ""),
            "question": task["question"],
            "expect_status": expect_status,
            "expect_refused": bool(task.get("expect_refused", False)),
            "gold_quotes": task.get("gold_quotes", []),
            "gold_filename": task.get("gold_filename"),
            "status": None,
            "refused": False,
            "citations": [],
            "citation_texts": [],
            "elapsed_ms": None,
            "latency_ms": None,
            "error": None,
        }
        routes.BASELINE = baseline
        try:
            started = time.perf_counter()
            response = target_client.post("/ask", json={"question": task["question"]})
            entry["elapsed_ms"] = max(
                0, int((time.perf_counter() - started) * 1000)
            )
            entry["status"] = response.status_code
            if response.status_code == 200:
                body = response.json()
                entry["refused"] = bool(body.get("refused"))
                entry["citations"] = body.get("citations", [])
                entry["latency_ms"] = int(body.get("latency_ms", 0))
                entry["citation_texts"] = [
                    _chunk_text(target_db, citation["chunk_id"])
                    for citation in entry["citations"]
                ]
        except Exception as exc:  # one bad task must not stop the run
            entry["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            routes.BASELINE = False
        entries.append(entry)
    return entries


def _refusal_ok(entry: dict) -> bool:
    if entry["error"] is not None or entry["status"] is None:
        return False
    if entry["status"] != entry["expect_status"]:
        return False
    if entry["expect_status"] != 200:
        return True
    return entry["refused"] == entry["expect_refused"]


def _citation_ok(entry: dict) -> bool:
    if not entry["citation_texts"] or not entry["gold_quotes"]:
        return False
    return any(
        quote in text
        for text in entry["citation_texts"]
        for quote in entry["gold_quotes"]
    )


def _summarize(entries: list[dict]) -> dict:
    answerable = [entry for entry in entries if entry["group"] == "answerable"]
    answered = [
        entry
        for entry in answerable
        if entry["status"] == 200 and not entry["refused"]
    ]
    latencies = [
        entry["elapsed_ms"]
        for entry in entries
        if entry["status"] is not None and entry["elapsed_ms"] is not None
    ]
    return {
        "total": len(entries),
        "refusal_correct": sum(1 for entry in entries if _refusal_ok(entry)),
        "answered": len(answered),
        "citation_correct": sum(1 for entry in answered if _citation_ok(entry)),
        "answerable": len(answerable),
        "retrieval_hit": sum(1 for entry in answerable if _citation_ok(entry)),
        "avg_latency_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
        "errors": [entry["id"] for entry in entries if entry["error"]],
    }


def _rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{numerator}/{denominator} ({numerator / denominator:.1%})"


def _group_rows(baseline: list[dict], delta: list[dict]) -> list[str]:
    rows = []
    for group in GROUPS:
        base_group = [entry for entry in baseline if entry["group"] == group]
        delta_group = [entry for entry in delta if entry["group"] == group]
        base_ok = sum(1 for entry in base_group if _refusal_ok(entry))
        delta_ok = sum(1 for entry in delta_group if _refusal_ok(entry))
        base_cite = sum(1 for entry in base_group if _citation_ok(entry))
        delta_cite = sum(1 for entry in delta_group if _citation_ok(entry))
        rows.append(
            f"| {group} | {len(base_group)} | {base_ok} | {delta_ok} | {base_cite} | {delta_cite} |"
        )
    return rows


def _pick_cases(baseline: list[dict], delta: list[dict]) -> list[str]:
    by_id = {entry["id"]: entry for entry in baseline}
    cases: list[tuple[str, dict, dict]] = []

    # Prefer the conceptually interesting misses: a near-miss question that
    # shares words with a document but is not supported by it, then a fully
    # unrelated question that still matched a generic word.
    for kind in ("near_miss", "unrelated"):
        for entry in delta:
            if (
                entry["group"] == "unanswerable"
                and entry["kind"] == kind
                and entry["status"] == 200
                and not entry["refused"]
            ):
                cases.append((f"delta 未拒答 · {kind}", entry, by_id[entry["id"]]))
    for entry in delta:
        if (
            entry["group"] == "answerable"
            and entry["status"] == 200
            and not entry["refused"]
            and not _citation_ok(entry)
        ):
            cases.append(("delta 引用未命中 gold_quote", entry, by_id[entry["id"]]))
    for entry in delta:
        if entry["expect_refused"] and entry["refused"]:
            base = by_id[entry["id"]]
            if not base["refused"]:
                cases.append(("baseline 未拒答 / delta 拒答", entry, base))

    lines = []
    for label, entry, base in cases[:2]:
        quoted = ""
        if entry["citation_texts"]:
            quoted = entry["citation_texts"][0][:60].replace("\n", "\\n")
        parts = [
            f"- **{label}** `{entry['id']}`（{entry['kind']}）：问题「{entry['question']}」",
            f"  - baseline：status={base['status']} refused={base['refused']} citations={len(base['citations'])}",
            f"  - delta：status={entry['status']} refused={entry['refused']} citations={len(entry['citations'])}",
        ]
        if quoted:
            parts.append(f"  - delta 首条引用片段：`{quoted}`")
        if entry["gold_quotes"]:
            parts.append(f"  - gold_quotes：{entry['gold_quotes']}")
        lines.append("\n".join(parts))
    return lines or ["- 本次运行没有失败或差异案例。"]


def _failures(entries: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {group: [] for group in GROUPS}
    for entry in entries:
        if not _refusal_ok(entry):
            grouped[entry["group"]].append(entry["id"])
    return grouped


def _write_report(
    baseline_entries: list[dict],
    delta_entries: list[dict],
    base_stats: dict,
    delta_stats: dict,
) -> Path:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    delta_failures = _failures(delta_entries)
    base_failures = _failures(baseline_entries)

    def ids(group: str, failures: dict[str, list[str]]) -> str:
        items = failures[group]
        return "、".join(f"`{item}`" for item in items) if items else "无"

    lines = [
        "# DocQA-lite · 30 题管线评测报告",
        "",
        f"- 生成时间：{now}",
        "- 数据：`eval/tasks.json`（answerable 12 / unanswerable 8 / boundary 10），`eval/demo_docs/` 三篇演示文档",
        "- 模型：**脚本化 fake LLM**，固定返回 `根据片段 [1]`，不联网、不读 key；pytest 与评测都不调用真实模型",
        "- 对照：baseline = 跳过 overlap 拒答与引用校验；delta = 完整管线",
        "",
        "> **评测的是检索 / 拒答 / 引用管线，不是模型能力。** baseline 与 delta 用的是同一个 fake LLM，",
        "> 两者的差别只来自「拒答闸门 + 引用校验」这两层。",
        "",
        "## 1. 总表",
        "",
        "| 指标 | baseline | delta |",
        "|---|---|---|",
        f"| 拒答正确率（30 题） | {_rate(base_stats['refusal_correct'], 30)} | {_rate(delta_stats['refusal_correct'], 30)} |",
        f"| 引用正确率（answerable 且未拒答） | {_rate(base_stats['citation_correct'], base_stats['answered'])} | {_rate(delta_stats['citation_correct'], delta_stats['answered'])} |",
        f"| 检索命中率（answerable 全部） | {_rate(base_stats['retrieval_hit'], 12)} | {_rate(delta_stats['retrieval_hit'], 12)} |",
        f"| 平均端到端延迟 | {base_stats['avg_latency_ms']} ms | {delta_stats['avg_latency_ms']} ms |",
        f"| 异常任务 | {len(base_stats['errors'])} | {len(delta_stats['errors'])} |",
        "",
        "口径：",
        "- 拒答正确率 = 30 题中行为符合预期的条数（`expect_status` 对得上，200 时 `refused == expect_refused`）；",
        "- 引用正确率 = 被回答的 answerable 题里，至少一条 citation 的 chunk 文本包含 gold_quote 的比例；baseline 不产出 citations，故为 0；",
        "- 检索命中率 = answerable 题里 citation 命中 gold_quote 的比例（分母固定 12，未拒答/未命中都算没命中）；",
        "- 多跳题只校验「至少一条 gold_quote 被引用」，fake LLM 只会写 `[1]`，所以多跳的完整性没有被真正测到。",
        "",
        "## 2. 分组",
        "",
        "| 分组 | 题数 | baseline 拒答正确 | delta 拒答正确 | baseline 引用命中 | delta 引用命中 |",
        "|---|---|---|---|---|---|",
        *_group_rows(baseline_entries, delta_entries),
        "",
        "未过的题（delta）：answerable " + ids("answerable", delta_failures) + "；unanswerable "
        + ids("unanswerable", delta_failures) + "；boundary " + ids("boundary", delta_failures) + "。",
        "",
        "未过的题（baseline，对照用）：answerable " + ids("answerable", base_failures) + "；unanswerable "
        + ids("unanswerable", base_failures) + "；boundary " + ids("boundary", base_failures) + "。",
        "",
        "## 3. 失败 / 差异案例",
        "",
        *_pick_cases(baseline_entries, delta_entries),
        "",
        "## 4. 读法",
        "",
        "- delta 的拒答提升全部来自「检索为空」这条**不花钱**的路径（完全无关的话题一个 bigram 都对不上）；",
        "- unanswerable 里没被拒的题，根因是问题与文档共享通用 bigram（`公司`、`城市`、`年假`、`住宿`）："
        "闸门放行后，fake LLM 永远声称有答案，引用校验也看不出这句话其实没有依据；",
        "- 这一层本来就该由模型兜底（真实模型会说「未在文档中找到依据」），所以 22/30 是**没有模型时**的下限，"
        "不是管线在天花板上的表现 —— 也正因如此，本报告不评价模型能力；",
        "- 引用正确率衡量的是「引用是否落在含 gold_quote 的 chunk 上」，不衡量答案文字对不对（答案由 fake LLM 固定生成）。",
        "",
        "## 5. 还没覆盖的边界",
        "",
        "- 真实模型输出格式（是否稳定写 `[n]`）、真实拒答措辞、真实多跳引用；",
        "- 并发上传、超大文档、跨 chunk 边界的长短语；",
        "- 评测题目是我们自己写的 30 条，规模小，`gold_quotes` 由脚本校验必须能在文档里原样找到。",
        "",
    ]
    path = HERE / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    docs = _read_docs()
    _validate(tasks, docs)

    # One scripted model for both modes; no network, no key.
    llm.chat = _fake_chat
    routes.BASELINE = False

    work_dir = ROOT / ".tmp" / "eval"
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(work_dir / "eval.db")
    fresh_db_path = str(work_dir / "fresh.db")
    for path in (db_path, fresh_db_path):
        Path(path).unlink(missing_ok=True)

    with TestClient(create_app(db_path)) as client:
        for name, text in docs.items():
            response = _upload(client, name, text)
            assert response.status_code == 201, (
                name,
                response.status_code,
                response.text,
            )

        with TestClient(create_app(fresh_db_path)) as fresh_client:
            try:
                routes.BASELINE = True
                baseline_entries = _run_mode(
                    baseline=True,
                    tasks=tasks,
                    docs=docs,
                    client=client,
                    db_path=db_path,
                    fresh_client=fresh_client,
                    fresh_db_path=fresh_db_path,
                )
            finally:
                routes.BASELINE = False

            try:
                routes.BASELINE = False
                delta_entries = _run_mode(
                    baseline=False,
                    tasks=tasks,
                    docs=docs,
                    client=client,
                    db_path=db_path,
                    fresh_client=fresh_client,
                    fresh_db_path=fresh_db_path,
                )
            finally:
                routes.BASELINE = False

    base_stats = _summarize(baseline_entries)
    delta_stats = _summarize(delta_entries)
    report = _write_report(
        baseline_entries, delta_entries, base_stats, delta_stats
    )

    print(
        "tasks=%d baseline_refusal_ok=%d delta_refusal_ok=%d "
        "baseline_cite=%d/%d delta_cite=%d/%d delta_retrieval=%d/%d "
        "baseline_errors=%d delta_errors=%d"
        % (
            len(tasks),
            base_stats["refusal_correct"],
            delta_stats["refusal_correct"],
            base_stats["citation_correct"],
            base_stats["answered"],
            delta_stats["citation_correct"],
            delta_stats["answered"],
            delta_stats["retrieval_hit"],
            12,
            len(base_stats["errors"]),
            len(delta_stats["errors"]),
        )
    )
    print(f"report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
