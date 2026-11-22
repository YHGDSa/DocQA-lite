# DocQA-lite

把内部 md/txt 文档变成「可引用、会拒答」的最小问答服务：上传 → 字符切片（带偏移）→ SQLite FTS5 检索 → 生成 → 引用校验 → SSE 流式输出。**不依赖向量库、图数据库或 rerank**，一个 SQLite 文件加一个 OpenAI 兼容的 chat 接口就能跑。

## 架构

```
上传 POST /documents/upload
   │  UTF-8 解码 → sha256 去重 → 字符切片(400/50) → documents + chunks
   ▼
SQLite：documents / chunks / chunks_fts(FTS5 trigram) / qa_logs
   │
   ├─ 检索 POST /search        FTS5 MATCH + bm25；<3 字或问句走 LIKE 兜底
   │
   └─ 问答 POST /ask           search_for_question → overlap 闸门 → LLM → 引用校验 → qa_logs
      流式 POST /ask/stream   citations → token* → done（SSE）
```

## Quickstart

```powershell
cd docqa-lite
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt

.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8001
.\.venv\Scripts\pytest.exe -q          # 或 ..\run-tests-b.ps1
.\.venv\Scripts\python.exe eval\run_eval.py
```

想接真实模型时，在**进程环境变量**里设置（本服务不读 `.env`）：

```powershell
$env:LLM_BASE_URL = "https://api.example.com/v1"
$env:LLM_API_KEY  = "<your key>"
$env:LLM_MODEL    = "your-model"
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{"ok": true}` |
| POST | `/documents/upload` | multipart `file`，只收 `.md` / `.txt`（≤2MB，UTF-8）。新文档 `201`，同 sha256 已存在 `200 duplicated=true`，空/纯空白 `400`，超限 `413` |
| GET | `/documents` | 未删除文档列表（id DESC），带 `chunks` 数 |
| POST | `/search` | `{"query": "...", "top_k": 5}` → 命中的 chunk + `score`/`overlap`/偏移 |
| POST | `/ask` | `{"question": "..."}` → `answer` + `citations` + `refused` + `latency_ms`；无 key `503` |
| POST | `/ask/stream` | 同 `/ask`，以 SSE 返回：`citations` → `token`* → `done` |

三个 curl：

```bash
curl -X POST http://127.0.0.1:8001/documents/upload -F "file=@handbook.md"
curl -X POST http://127.0.0.1:8001/search -H "Content-Type: application/json" -d '{"query":"年假 10 天","top_k":5}'
curl -N -X POST http://127.0.0.1:8001/ask -H "Content-Type: application/json" -d '{"question":"年假多少天"}'
```

流式（`-N` 关闭 curl 缓冲）：

```bash
curl -N -X POST http://127.0.0.1:8001/ask/stream -H "Content-Type: application/json" -d '{"question":"年假多少天"}'
```

## 单位与偏移

| 字段 | 含义 |
|---|---|
| `documents.size` | 上传字节数（UTF-8 **字节**，不是字符数） |
| `chunks.char_start` / `char_end` | **Python 字符**索引（中文一个字算 1），`char_end` 不包含 |
| `chunks.line_start` / `line_end` | 1 起算的**行号**，两端都包含 |
| `citations.line_start/line_end` | 同上，给的是行号，不是字节偏移；`quote` 是被引 chunk 的前 200 字 |

`documents.size` 用字节，是因为它要回答「这个文件多大」；`char_*` 用字符，是因为 `text[char_start:char_end]` 必须能原样把片段切回来（中文 1 字 3 字节，按字节切会对不上）。

## 设计取舍

- **为什么 400 字 / 50 重叠**：语料是几十篇内部文档，400 字正好装下一个条款，命中后不用再切；重叠 50 保证跨段的话不会被切断。LightRAG 默认 1200 token / 100 token 是给大语料 + 图抽取用的，见 `docs/REFERENCE-B.md`。
- **为什么 trigram + LIKE 两条路**：trigram 对中英文都不用分词、支持子串匹配，但**小于 3 个字符的查询没有 trigram 可查**，所以 1—2 字走参数化 LIKE（`%`/`_` 已转义）；问句问法（"年假多少天"）不是文档子串，`/ask` 额外用问题的 2 字窗口做一遍 LIKE 兜底。
- **为什么拒答看 overlap 而不是 bm25**：bm25 的量级随语料和 chunk 长度漂移（本地实测 1e-6 量级），没法定跨语料阈值；`overlap` 是「问题里的字符 bigram 有多少出现在这段里」，与语料规模无关。
- **为什么流式不做最终引用校验**：SSE 是边生成边推，模型可能在最后一个 token 才写出 `[n]`，中途没法判定；所以 `/ask/stream` 的 `citations` 事件是**候选**（检索到的 1..n 段），最终「这条引用是否合法」由 `/ask` 决定。要拿到校验后的答案就用 `/ask`。

## 评测（30 题，baseline vs delta）

`python eval/run_eval.py` → `eval/report.md`。题目 12 answerable / 8 unanswerable / 10 boundary，模型是**脚本化 fake LLM**（固定返回 `根据片段 [1]`），不联网、不读 key。

> **评测的是检索 / 拒答 / 引用管线，不是模型能力。**

| 指标 | baseline（跳过拒答+引用校验） | delta（完整管线） |
|---|---|---|
| 拒答正确率（30 题） | 17/30 (56.7%) | **22/30 (73.3%)** |
| 引用正确率（answerable 且未拒答） | 0/12 (0.0%) | **12/12 (100.0%)** |
| 检索命中率（answerable 全部） | 0/12 (0.0%) | **12/12 (100.0%)** |
| 平均端到端延迟 | ~6 ms | ~6 ms |

delta 没拒掉的 7 条 unanswerable 都共享通用词（`公司`、`城市`、`年假`），闸门放行后 fake LLM 永远声称有答案——这一层要靠真实模型兜底。详见 `eval/report.md` 的「失败 / 差异案例」与「读法」。

## 已知限制

- **并发重复上传**：`UNIQUE(sha256)` 冲突已被捕获并回查，返回 `200 duplicated=true`；软删场景返回 `409`。没有真正的并发压测。
- **跨 chunk 短语**：被 400 字窗口切断的长短语，FTS5 短语查询可能不命中（50 字重叠只缓解）。
- **空文档**：已拒绝（`400`），不会留下 0 chunk 的文档。
- **SQLite 单进程**：写操作串行，没有连接池；多进程部署要换数据库。
- **无分页**：`/documents` 全量返回，文档数没有配额。
- **引用格式很严**：只认裸 `[n]`；`[1,2]`、`【1】` 会被当作无引用而降级为拒答。
- **流式不做最终校验**（见上），只给候选 citations。

## 安全

- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 只从**进程环境变量**读取，不读 `.env`、不打印、不写日志、不进 `qa_logs`；仓库只有 `.env.example` 占位符。
- 没有 key 时 `/ask` 返回 `503`（不是 500），`/ask/stream` 在开始流之前返回 `503`；无证据的拒答路径完全不需要 key。
- 所有 SQL 参数化；上传文件名只取 basename，不落盘。
