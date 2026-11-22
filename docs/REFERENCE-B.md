# REFERENCE-B · 从 LightRAG 借的两件事：切片元数据 + 引用回原文

> 读于 2026-11-22。B 项目（DocQA-lite）只借思路与字段形状，不复制代码。

## 1. 仓库 / 分支 / 日期

- 仓库：`HKUDS/LightRAG`（GitHub）
- 分支：`main`
- 读取时最新提交：`f01ddea`（2026-11-22T03:13:23Z）
- 读取日期：2026-11-22
- 路径说明：任务里写的 `lightrag/chunker.py` 在当前 main 上已拆成包 `lightrag/chunker/`，默认策略在 `chunker/token_size.py`；我读的是后者，没有读包里的 `paragraph_semantic.py` / `semantic_vector.py` / `recursive_character.py`。

## 2. 我实际读了哪两个文件、哪几个函数

文件一：`lightrag/chunker/token_size.py`（另看了同目录 `__init__.py` 的调度说明）

- `chunking_by_token_size(tokenizer, content, split_by_character, split_by_character_only, chunk_overlap_token_size=100, chunk_token_size=1200)`：默认固定 token 窗口切片，保留旧 6 参签名做兼容；
- `_make_chunk(...)`：单个 chunk 的元数据组装；
- `_window_step(...)`：步长 = `chunk_token_size - chunk_overlap_token_size`，并要求 overlap < size，否则直接报错；
- `_source_span(...)` / `_token_window_source_span(...)`：把 token 窗口映射回字符区间。

文件二：`lightrag/operate.py`

- `naive_query(...)`（约 7718 行）：取 chunk → 按 token 预算裁剪 → 生成引用列表 → 拼上下文 → 调模型；
- `_build_context_str(...)`（约 6662 行）与 `_fit_kg_context_to_total_budget(...)`（约 6397 行）：`kg_query` 和 naive 共用的上下文拼装与引用编号；
- `_process_single_content(...)` 的 docstring（约 4616 行）：写明 chunk 键和字段形状。

为了追“引用编号到底在哪生成”，另外读了 `lightrag/utils.py` 的 `generate_reference_list_from_chunks(...)` 和 `render_chunks_context_text(...)` 两个函数；`utils.py` 的其它部分没读。

## 3. LightRAG 怎么保存 chunk 元数据、怎么把引用带回原文

- 切片：按 token 滑窗，默认窗口 1200、重叠 100（步长 1100）；每个 chunk 记 `tokens`、`content`（strip 后）、`chunk_order_index`，可选 `_source_span = {"start": 字符偏移, "end": 字符偏移}`。
- 存储：chunk 以 `chunk-<hash>` 为键存进 text_chunks KV，值形状是 `{"tokens": int, "content": str, "full_doc_id": str, "chunk_order_index": int}`。也就是说，chunk 知道自己是哪篇文档的第几片，但**没有行列号**；能回原文靠的是 chunk 自带的 `file_path`。
- 引用：`generate_reference_list_from_chunks` 按 `file_path` 去重，按“出现次数降序 + 首次出现顺序”编号 `"1"`, `"2"`……；`render_chunks_context_text` 把每个 chunk 压成 `{"reference_id", "content"}` 一行 JSON 喂给模型；上下文末尾再附 `[1] file_path` 形式的来源清单；返回给调用方的 raw_data 里 chunk 带 `reference_id`，前端据此把答案里的 `[n]` 对回文件。
- 关键限制：**它的引用粒度是文件，不是位置**。字符级的 `_source_span` 只在切片内部用（而且要显式 `_emit_source_span=True` 才带），查询链路没有把它暴露给用户。

## 4. 2 周版本为什么不做图 / 向量

- 语料小：内部文档在几十篇量级，切片能整库进内存，BM25（SQLite FTS5）取前几条就够；
- 图的成本在抽取：实体/关系抽取要多次 LLM 调用、要 prompt 调优、要合并去重，2 周会把时间吃光，而在几十篇语料上收益不明显；
- 向量要 embedding 服务 + 索引 + 阈值调参，且“语义相似”不等于“答得对”，阈值调不好反而更难解释；
- 引用可验证：BM25 命中 chunk 后能直接给出字符偏移和行号，答案里的 `[n]` 可以回到原文具体位置核对——这正是本项目要证明的东西，图/向量不帮这个忙。

## 5. 我借了什么、没借什么

借了：

- chunk 元数据的字段形状：`tokens`（我们记切片长度）、`chunk_order_index`（我们用 `seq`）、所属文档 id、切片正文；
- 滑窗 + 重叠的思路（默认 1200/100 token → 我们按字符切、重叠也按字符算）；
- 引用编号方式：检索结果去重后编 `[1..n]`，上下文里逐条 `{reference_id, content}`，末尾附来源清单；
- “每个 chunk 必须能独立回到原文”这条验收标准。

没借：

- 实体/关系抽取、图存储、图检索（`kg_query` 那条线）；
- 向量库、embedding、rerank、关键词抽取、gleaning、LLM 摘要合并；
- 它的 token 预算体系（`max_total_tokens` 等），我们用更粗的字符/条数上限。

一句话：**不是复现 LightRAG，只借切片元数据 + 引用思路**。我们比它多记一层 `char_start` / `char_end` / `line_start` / `line_end`，让引用落到原文位置，而不只是文件名。
