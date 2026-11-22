# 真实模型小评测（12 题）

- 生成时间：2026-11-22 11:25:08 UTC
- 模型：`deepseek-chat`（OpenAI 兼容 chat/completions；key 与 base URL 不写入任何文件）
- 语料：`eval/demo_docs/` 三篇演示文档（合成样例，不含任何真实内部资料）
- 题目：`eval/real_tasks.json`（answerable 4 / unrelated 4 / near_miss 4）

## 指标

- 答案正确：4/4（answerable 题里关键词全部命中）
- 证据支持：4/4（被引 chunk 原文包含 gold_quote）
- 无答案题拒答：8/8（unrelated + near_miss）
- 异常：0

## 逐题

| # | 类别 | 问题 | refused | 引用 | 答案（截断 70 字） | 自动判定 |
|---|---|---|---|---|---|---|
| r01 | answerable | 年假有多少天，没休完怎么处理 | False | 1 | 年假有 10 天 [1]。未休完可结转至次年一季度 [1]。 | 答案✓ 证据✓ |
| r02 | answerable | 差旅报销多久到账 | False | 1 | 差旅报销财务在 5 个工作日内到账 [1]。 | 答案✓ 证据✓ |
| r03 | answerable | 密码多久必须换一次 | False | 1 | 公司账号密码每 90 天必须更换一次 [1]。 | 答案✓ 证据✓ |
| r04 | answerable | 一线城市住宿标准是多少 | False | 1 | 一线城市住宿标准每晚 600 元 [1]。 | 答案✓ 证据✓ |
| r05 | unrelated | 公司有没有股票期权 | True | 0 | 未在文档中找到依据。 | 拒答✓ |
| r06 | unrelated | 服务器机房在哪个城市 | True | 0 | 未在文档中找到依据。 | 拒答✓ |
| r07 | unrelated | 公司年会在什么时候办 | True | 0 | 未在文档中找到依据。 | 拒答✓ |
| r08 | unrelated | 我们的竞争对手是哪家公司 | True | 0 | 未在文档中找到依据。 | 拒答✓ |
| r09 | near_miss | 年假可以折现吗 | True | 0 | 未在文档中找到依据。 | 拒答✓ |
| r10 | near_miss | 病假期间的工资怎么算 | True | 0 | 未在文档中找到依据。 | 拒答✓ |
| r11 | near_miss | 住宿标准包含餐饮吗 | True | 0 | 未在文档中找到依据。 | 拒答✓ |
| r12 | near_miss | 出差可以坐高铁一等座吗 | True | 0 | 未在文档中找到依据。 | 拒答✓ |

## 读法

- 「答案正确」只看关键词是否出现在答案里，不理解语义；「证据支持」只看被引 chunk 原文是否包含 gold_quote，
  两者都不能替代人工判断，人工复核见 `docs/REAL-LLM.md`；
- near_miss 题如果模型诚实地说「文档未提及 X，只提到 Y [1]」，会得到 `refused=false` 但引用合法，
  本表会记成「拒答✗」——这类边界需要人工读答案，报告里单独说明。
