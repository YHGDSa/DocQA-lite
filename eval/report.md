# DocQA-lite · 30 题管线评测报告

- 生成时间：2026-11-22 15:33:23 UTC
- 数据：`eval/tasks.json`（answerable 12 / unanswerable 8 / boundary 10），`eval/demo_docs/` 三篇演示文档
- 模型：**脚本化 fake LLM**，固定返回 `根据片段 [1]`，不联网、不读 key；pytest 与评测都不调用真实模型
- 对照：baseline = 跳过 overlap 拒答与引用校验；delta = 完整管线

> **评测的是检索 / 拒答 / 引用管线，不是模型能力。** baseline 与 delta 用的是同一个 fake LLM，
> 两者的差别只来自「拒答闸门 + 引用校验」这两层。

## 1. 总表

| 指标 | baseline | delta |
|---|---|---|
| 拒答正确率（30 题） | 17/30 (56.7%) | 22/30 (73.3%) |
| 引用正确率（answerable 且未拒答） | 0/12 (0.0%) | 12/12 (100.0%) |
| 检索命中率（answerable 全部） | 0/12 (0.0%) | 12/12 (100.0%) |
| 平均端到端延迟 | 9.6 ms | 8.3 ms |
| 异常任务 | 0 | 0 |

口径：
- 拒答正确率 = 30 题中行为符合预期的条数（`expect_status` 对得上，200 时 `refused == expect_refused`）；
- 引用正确率 = 被回答的 answerable 题里，至少一条 citation 的 chunk 文本包含 gold_quote 的比例；baseline 不产出 citations，故为 0；
- 检索命中率 = answerable 题里 citation 命中 gold_quote 的比例（分母固定 12，未拒答/未命中都算没命中）；
- 多跳题只校验「至少一条 gold_quote 被引用」，fake LLM 只会写 `[1]`，所以多跳的完整性没有被真正测到。

## 2. 分组

| 分组 | 题数 | baseline 拒答正确 | delta 拒答正确 | baseline 引用命中 | delta 引用命中 |
|---|---|---|---|---|---|
| answerable | 12 | 12 | 12 | 0 | 12 |
| unanswerable | 8 | 0 | 1 | 0 | 0 |
| boundary | 10 | 5 | 9 | 0 | 3 |

未过的题（delta）：answerable 无；unanswerable `t13`、`t15`、`t16`、`t17`、`t18`、`t19`、`t20`；boundary `b05`。

未过的题（baseline，对照用）：answerable 无；unanswerable `t13`、`t14`、`t15`、`t16`、`t17`、`t18`、`t19`、`t20`；boundary `b05`、`b06`、`b07`、`b08`、`b10`。

## 3. 失败 / 差异案例

- **delta 未拒答 · near_miss** `t18`（near_miss）：问题「年假可以折现吗」
  - baseline：status=200 refused=False citations=0
  - delta：status=200 refused=False citations=1
  - delta 首条引用片段：`# 员工手册\n\n## 第一章 假期\n\n员工年假 10 天，需提前一周申请，未休完可结转至次年一季度。\n\n病假需提供医院证`
- **delta 未拒答 · near_miss** `t19`（near_miss）：问题「病假期间的工资怎么算」
  - baseline：status=200 refused=False citations=0
  - delta：status=200 refused=False citations=1
  - delta 首条引用片段：`# 员工手册\n\n## 第一章 假期\n\n员工年假 10 天，需提前一周申请，未休完可结转至次年一季度。\n\n病假需提供医院证`

## 4. 读法

- delta 的拒答提升全部来自「检索为空」这条**不花钱**的路径（完全无关的话题一个 bigram 都对不上）；
- unanswerable 里没被拒的题，根因是问题与文档共享通用 bigram（`公司`、`城市`、`年假`、`住宿`）：闸门放行后，fake LLM 永远声称有答案，引用校验也看不出这句话其实没有依据；
- 拒答正确率取决于本报告的题目、检索配置和固定回答脚本。真实模型使用独立的评测，结果见 `docs/REAL-LLM.md`；
- 引用正确率衡量的是「引用是否落在含 gold_quote 的 chunk 上」，不衡量答案文字对不对（答案由 fake LLM 固定生成）。

## 5. 还没覆盖的边界

- 真实模型输出格式（是否稳定写 `[n]`）、真实拒答措辞、真实多跳引用；
- 并发上传、超大文档、跨 chunk 边界的长短语；
- 评测题目是我们自己写的 30 条，规模小，`gold_quotes` 由脚本校验必须能在文档里原样找到。
