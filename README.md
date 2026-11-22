# DocQA-lite

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![tests](https://img.shields.io/badge/tests-14%20passed-brightgreen)](tests)
[![eval](https://img.shields.io/badge/offline%20eval-30%20tasks-brightgreen)](eval/report.md)

把 md/txt 文档变成**可引用、会拒答**的问答服务：上传 → 字符切片（带字符偏移与行号）→ SQLite FTS5 检索 → LLM 生成 → 引用校验 → SSE 流式输出。**不用向量库、图数据库或 rerank**：一个 SQLite 文件 + 任意 OpenAI 兼容 chat 接口即可运行。

## 30 秒无 Key 演示

```bash
python -m venv .venv && .venv\Scripts\activate    # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python scripts/demo_offline.py
```

真实输出（模型是 10 行脚本，所以逐字可复现；演示的是**管线**，不是模型能力）：

```
== 1. 上传三篇演示文档 ==
  handbook.md    HTTP 201  chunks=1
  security.md    HTTP 201  chunks=1
  travel.md      HTTP 201  chunks=1

== 2. 检索 "年假 10 天" ==
  [1] handbook.md 第1-13行  chars 0-133  score=0.484 overlap=1.000
      # / 员工手册 / ## / 第一章 / 假期 / 员工年假 / 10 / 天，需提前一周申请，未休完可结转至次年一季度。 /…

== 3. /ask "年假多少天"（脚本化模型，无需 Key）==
  refused   : False
  answer    : 员工年假 10 天，未休完可结转至次年一季度 [1]
  citation  : [1] handbook.md 第1-13行  chunk_id=1

== 4. /ask "公司有没有股票期权"（文档里没有）==
  refused   : True
  answer    : 未在文档中找到依据。
  citations : []

== 5. /ask/stream "年假多少天"（SSE 事件顺序）==
  citations : 1 个候选
  events    : citations -> token -> token -> token -> token -> token -> done
```

## 架构

```
上传 POST /documents/upload
   │  UTF-8 → sha256 去重 → 400 字切片 / 50 重叠（记字符偏移 + 行号）
   ▼
SQLite: documents / chunks / chunks_fts(FTS5 trigram) / qa_logs
   │
   ├─ POST /search        FTS5 MATCH + bm25；<3 字与问句走参数化 LIKE 兜底
   ├─ POST /ask           search_for_question → overlap 闸门 → LLM → 引用校验 → qa_logs
   └─ POST /ask/stream    SSE: citations（候选）→ token* → done
```

| 目录 | 内容 |
|---|---|
| `app/` | FastAPI 应用：`routes`（HTTP）、`retrieval`（FTS5 + LIKE）、`chunking`（字符切片）、`citations`（引用校验）、`llm`（OpenAI 兼容客户端）、`db`（DDL） |
| `tests/` | 14 个 pytest：健康检查、上传/去重/空文档、检索偏移、短查询、引用、拒答、SSE |
| `eval/` | 30 题离线管线评测（脚本化模型）+ 12 题真实模型评测 + 三篇演示文档 |
| `docs/` | `REAL-LLM.md`（真实模型验收逐题结果）、`REFERENCE-B.md`（切片/引用思路来源） |
| `scripts/` | `demo_offline.py`（无 Key 演示）、`run_tests.py`（沙箱友好的测试入口） |

## 安装与验证（干净检出）

```bash
git clone <this-repo> && cd docqa-lite
python -m venv .venv
.venv\Scripts\activate                  # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python -m pytest -q                     # 14 passed
python eval/run_eval.py                 # 30 题离线评测，写 eval/report.md
python scripts/demo_offline.py          # 上面的无 Key 演示
```

在某些沙箱环境里系统临时目录不可写（pytest 会在建 basetemp 时 `PermissionError`），用
`python scripts/run_tests.py` —— 它只把 `TMP/TEMP` 指到仓库内 `.tmp/`，测试代码不变。

CI（`.github/workflows/ci.yml`）在 Ubuntu + Windows、Python 3.12 上跑：`pytest` → 离线 30 题评测 → 无 Key 演示。

## 接真实模型

服务只从**进程环境变量**读取配置，**不读 `.env`**：

```powershell
$env:LLM_BASE_URL = "https://api.example.com/v1"
$env:LLM_API_KEY  = "<your key>"
$env:LLM_MODEL    = "your-model"
python -m uvicorn app.main:app --port 8001
```

```bash
# Linux/macOS
export LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=...
```

配置好后跑真实模型小评测：

```bash
python eval/run_llm_eval.py     # 12 题；未配置时打印 REAL_LLM: SKIPPED_NO_KEY 并退出 0
```

`llm.py` 只发 `POST {LLM_BASE_URL}/chat/completions`，`Authorization: Bearer <key>`，
`temperature=0`、`max_tokens=512`、超时 60s；key 不打印、不写日志、不进 `qa_logs`。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{"ok": true}` |
| POST | `/documents/upload` | multipart `file`，只收 `.md`/`.txt`（≤2MB，UTF-8）。新建 `201`，同 sha256 已存在 `200 duplicated=true`，空/纯空白 `400`，超限 `413`，非 UTF-8 `400` |
| GET | `/documents` | 未删除文档（id DESC），带 `chunks` 数 |
| POST | `/search` | `{"query":"...","top_k":5}` → 命中 chunk + `score`/`overlap`/`char_*`/`line_*` |
| POST | `/ask` | `{"question":"..."}` → `answer` + `citations` + `refused` + `latency_ms`；未配置 `503`，上游错误 `502` |
| POST | `/ask/stream` | 同 `/ask`，SSE：`citations` → `token`* → `done`；未配置时在开流前 `503` |

```bash
curl -X POST http://127.0.0.1:8001/documents/upload -F "file=@eval/demo_docs/handbook.md"
curl -X POST http://127.0.0.1:8001/search -H "Content-Type: application/json" -d '{"query":"年假 10 天","top_k":5}'
curl -X POST http://127.0.0.1:8001/ask    -H "Content-Type: application/json" -d '{"question":"年假多少天"}'
curl -N -X POST http://127.0.0.1:8001/ask/stream -H "Content-Type: application/json" -d '{"question":"年假多少天"}'
```

## SSE 与引用校验

`/ask/stream` 先发 `citations`（本次检索到的 **1..n 段候选**），再逐个 `token`，最后 `done`；
模型输出里的 `[n]` 合法性**不在流里校验**——生成是流式的，模型可能把 `[n]` 写到最后一个 token。

`/ask` 才对引用做最终校验：只认裸 `[n]`，越界（`n > len(hits)`）或完全没有 `[n]` 一律降级为固定拒答
`未在文档中找到依据。`。所以：

- 要“经过校验、可以核对”的引用 → 用 `/ask`；
- 要“边生成边看、先知道会用哪些片段” → 用 `/ask/stream`，流里的 `citations` 是候选。

上游失败时：`/ask` 返回 `502`（流式场景则是 `200` → `error` 事件 → `done`）；未配置时 `/ask` 与
`/ask/stream` 都是 `503`，且后者在开流前返回。响应体只含状态码，不含 key。

## 单位与偏移

| 字段 | 含义 |
|---|---|
| `documents.size` | 上传字节数（UTF-8 **字节**） |
| `chunks.char_start` / `char_end` | **Python 字符**索引（中文 1 字算 1），`char_end` 不包含 |
| `chunks.line_start` / `line_end` | 1 起算的行号，两端都包含 |
| `citations.line_start` / `line_end` | 行号（不是字节偏移）；`quote` 是被引 chunk 的前 200 字 |

`text[char_start:char_end]` 能原样切回片段，是因为偏移按字符算；中文 1 字 3 字节，按字节切会对不上。

## 设计取舍

- **400 字 / 50 重叠**：语料是几十篇文档，400 字正好装下一个条款；重叠保证跨段的话不被切断。
  （LightRAG 默认 1200 token / 100 token 是给大语料 + 图抽取的，见 `docs/REFERENCE-B.md`。）
- **trigram + LIKE 两条路**：trigram 不用分词、支持子串，但 **<3 字没有 trigram 可查**，所以 1—2 字走参数化
  LIKE（`%`/`_` 已转义）；问句（"年假多少天"）不是文档子串，`/ask` 再用问题的 2 字窗口做一遍 LIKE 兜底。
- **拒答看 overlap，不看 bm25**：bm25 量级随语料漂移（实测 1e-6），没法定跨语料阈值；
  `overlap` 是“问题里的字符 bigram 有多少出现在这段里”，与语料规模无关。
- **流式不做最终校验**（见上）。这是一个有意的取舍，不是遗漏。

## 评测

### 离线 30 题（脚本化模型，`python eval/run_eval.py`）

题目 12 answerable / 8 unanswerable / 10 boundary；baseline 跳过拒答与引用校验，delta 是完整管线。
**评测的是检索 / 拒答 / 引用管线，不是模型能力**——两组用同一个固定回答 `根据片段 [1]` 的假模型。

| 指标 | baseline | delta |
|---|---|---|
| 拒答正确率（30 题） | 17/30 (56.7%) | **22/30 (73.3%)** |
| 引用正确率（answerable 且未拒答） | 0/12 | **12/12 (100%)** |
| 检索命中率（answerable 全部） | 0/12 | **12/12 (100%)** |
| 平均端到端延迟 | ~6 ms | ~6 ms |

delta 没拒掉的 7 条 unanswerable 都共享通用词（`公司`/`城市`/`年假`），放行后假模型永远声称有答案：
这 22/30 是**没有真模型时的下限**。同一批题接到真实模型后，8 条无答案题全部正确拒答（见下）。

### 真实模型 12 题（`python eval/run_llm_eval.py`）

模型 `deepseek-chat`，只用 `eval/demo_docs/` 三篇合成文档，逐题结果见 [docs/REAL-LLM.md](docs/REAL-LLM.md)。

| 指标 | 结果 |
|---|---|
| 答案正确（answerable） | **4/4** |
| 证据支持（被引 chunk 含对应原文） | **4/4** |
| 无答案题拒答（完全无关 4 + 近似但不支持 4） | **8/8** |
| 异常 | 0 |

其中「年假可以折现吗」「病假期间的工资怎么算」——离线评测里被假模型答上来的两道题——真实模型全部
正确拒答。逐题答案、引用行号、以及一轮 prompt 修复前后的对照都在 `docs/REAL-LLM.md`。

## 已知限制

- **并发重复上传**：捕获 `IntegrityError` 后回查，返回 `200 duplicated=true`；软删残留返回 `409`。没有真并发压测。
- **跨 chunk 短语**：被 400 字窗口切断的长短语，FTS5 短语查询可能不命中（50 字重叠只缓解）。
- **引用粒度**：`quote` 是 chunk 前 200 字；一个 chunk 含多条信息时，引用无法精确到句。
- **拒答依赖模型**：闸门只挡“检索为空/覆盖度过低”，其余靠模型按 prompt 拒答；换模型要重测。
- **SQLite 单进程 / 无分页 / 无配额**，多进程部署要换数据库。
- **引用格式严格**：只认裸 `[n]`；`[1,2]`、`【1】` 视为无引用 → 拒答。
- **流式不做最终校验**（见上）。

## 安全

- `LLM_*` 只从进程环境变量读取；仓库里没有 `.env`、没有 key、没有数据库、没有真实文档。
- 无 key 时 `/ask` 返回 `503`（不是 500）；上游错误只回状态码，不回 key。
- 所有 SQL 参数化；上传只取文件名 basename，不落盘；`.env` / `*.db` / `data/` / `.tmp/` 已在 `.gitignore`。

## 验证状态

- **干净检出**（`git archive HEAD` → 空目录 → 新建 venv → `pip install -r requirements.txt`）：
  安装成功、`python -m pytest -q` → 14 passed、`python eval/run_eval.py` → 30 题通过、
  `python scripts/demo_offline.py` 逐字复现上面的输出。
- **真实模型**（`deepseek-chat`）：上传 → 检索 → `/ask` → `/ask/stream` 冒烟通过；
  12 题小评测 4/4 答案、4/4 证据支持、8/8 无答案题拒答，逐题见 [docs/REAL-LLM.md](docs/REAL-LLM.md)。
- **CI**（`.github/workflows/ci.yml`）：Ubuntu + Windows、Python 3.12，依次跑
  测试 → 离线 30 题评测 → 报告校验 → 无 Key 演示；推送仓库后生效。

## License

[MIT](LICENSE)
