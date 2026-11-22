"""Offline, no-API-key demo of the whole pipeline.

Seeds the three demo documents, then walks upload -> search -> ask -> ask/stream
with a short scripted model, so the transcript is deterministic and needs
neither network nor a key. It demonstrates the *pipeline* (retrieval, citation
validation, refusal, SSE framing), not model quality.

Usage:

    python scripts/demo_offline.py            # print the transcript
    python scripts/demo_offline.py --out X.md # also write it to X (UTF-8)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import config, llm  # noqa: E402
from app.main import create_app  # noqa: E402

DEMO_DOCS = ROOT / "eval" / "demo_docs"
DB_PATH = ROOT / ".tmp" / "demo" / "demo.db"

#: A model we can reproduce byte for byte: it answers only the question it was
#: scripted for and refuses everything else, which is exactly the contract the
#: prompt asks a real model to follow.
SCRIPTED = {
    "年假多少天": "员工年假 10 天，未休完可结转至次年一季度 [1]",
}

_OUT: list[str] = []


def _force_utf8_stdout() -> None:
    """Windows consoles/CI pipes default to a legacy code page (cp1252), which
    cannot encode the Chinese transcript. Reconfigure instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # non-text streams, old Python
            pass


def say(line: str = "") -> None:
    print(line)
    _OUT.append(line)


def _scripted_answer(messages: list[dict]) -> str:
    question = messages[-1]["content"].rsplit("问题：", 1)[-1].strip()
    return SCRIPTED.get(question, config.REFUSAL_TEXT)


async def scripted_chat(messages, temperature: float = 0.0) -> str:
    return _scripted_answer(messages)


async def scripted_stream(messages, temperature: float = 0.0):
    answer = _scripted_answer(messages)
    for index in range(0, len(answer), 6):
        yield answer[index : index + 6]


def _upload(client: TestClient, path: Path) -> dict:
    response = client.post(
        "/documents/upload",
        files={"file": (path.name, path.read_bytes(), "text/markdown")},
    )
    return {"status": response.status_code, **response.json()}


def main(argv: list[str]) -> int:
    _force_utf8_stdout()
    out_path = None
    if "--out" in argv:
        out_path = Path(argv[argv.index("--out") + 1]).resolve()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)

    llm.chat = scripted_chat  # type: ignore[assignment]
    llm.stream_chat = scripted_stream  # type: ignore[assignment]
    llm.is_configured = lambda: True  # type: ignore[assignment]

    with TestClient(create_app(str(DB_PATH))) as client:
        say("== 1. 上传三篇演示文档 ==")
        for name in ("handbook.md", "security.md", "travel.md"):
            body = _upload(client, DEMO_DOCS / name)
            say(f"  {body['filename']:<14} HTTP {body['status']}  chunks={body['chunks']}")

        say('')
        say('== 2. 检索 "年假 10 天" ==')
        hits = client.post("/search", json={"query": "年假 10 天", "top_k": 3}).json()["hits"]
        for index, hit in enumerate(hits, start=1):
            say(
                f"  [{index}] {hit['filename']} 第{hit['line_start']}-{hit['line_end']}行"
                f"  chars {hit['char_start']}-{hit['char_end']}"
                f"  score={hit['score']:.3g} overlap={hit['overlap']:.3f}"
            )
            one_line = " / ".join(hit["text"].split())
            say(f"      {one_line[:64]}…")

        say('')
        say('== 3. /ask "年假多少天"（脚本化模型，无需 Key）==')
        asked = client.post("/ask", json={"question": "年假多少天"}).json()
        say(f"  refused   : {asked['refused']}")
        say(f"  answer    : {asked['answer']}")
        for citation in asked["citations"]:
            say(
                f"  citation  : [{citation['index']}] {citation['filename']}"
                f" 第{citation['line_start']}-{citation['line_end']}行"
                f"  chunk_id={citation['chunk_id']}"
            )

        say('')
        say('== 4. /ask "公司有没有股票期权"（文档里没有）==')
        refused = client.post("/ask", json={"question": "公司有没有股票期权"}).json()
        say(f"  refused   : {refused['refused']}")
        say(f"  answer    : {refused['answer']}")
        say(f"  citations : {refused['citations']}")

        say('')
        say('== 5. /ask/stream "年假多少天"（SSE 事件顺序）==')
        with client.stream(
            "POST", "/ask/stream", json={"question": "年假多少天"}
        ) as response:
            names: list[str] = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    names.append(line.split(":", 1)[1].strip())
                elif line.startswith("data:") and names and names[-1] == "citations":
                    payload = json.loads(line.split(":", 1)[1].strip())
                    say(f"  citations : {len(payload['citations'])} 个候选")
        say(f"  events    : {' -> '.join(names)}")

    say('')
    say("完成：以上全部离线可复现（模型是脚本，不是真实 LLM）。")

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(_OUT) + "\n", encoding="utf-8")
        print(f"transcript: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
