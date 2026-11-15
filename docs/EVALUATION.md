# 评测（Evaluation）

本仓库保留一组可复现的检索评测执行器，代码在 `bench/`。公开仓**不包含真实语料、题集、金标或结果文件**；`bench/dataset/`、`bench/results/` 需按 [BYO_CORPUS.md](BYO_CORPUS.md) 自备。

评测范围是检索层和上下文组装层，不是公开 benchmark。所有数字都是本项目历史聚合结果；没有外部基线对照。

## 1. 指标

| 指标 | 定义 |
|---|---|
| `page@K` | 目标 wiki 页是否进入 top-K |
| `gold@K` | 目标页或目标 raw 是否进入 top-K；证据查询以 raw 为金标 |
| `MRR` | 第一个命中金标的排名倒数均值 |
| `sel_ratio` | 最终上下文中命中目标的比例 |
| `SHR` | 池内已命中时，最终入选的比例：`P(入选 | 池内命中)` |
| `latency` | 查询 p50 / p95 |
| `answer quality` | 可选 LLM 裁判 1–5 分，次级检查 |

`page@K` 和 `gold@K` 不可混用。58 集两者重合；hard50 的 `gold@K` 把原始证据计入命中，因此可能高于 `page@K`。

## 2. 评测集

| 集合 | 规模 | 覆盖 |
|---|---|---|
| 58 自然语言 | 58 查询 / 30 目标 | direct、indirect、多跳、跨源、全览 |
| hard50 | 50 | 长文档、多来源、跨章节、专有名词、隐含关系、原始证据 |
| 19 精确名称 | 19 | 实体/概念名本身 |
| 24 字面串 | 24（dev 17 / test 7） | 转义码、日期片段、编号+名称、负例 |

公开仓不附带题集；请用自己的同结构 JSONL。

## 3. 最终指标

推荐配置：`lex_leg=conditional` + `budget_policy=density`，balanced 20 / deep 16，balanced context 12000。

| 集合 | page@5 | page@10 | gold@5 | gold@10 | MRR | sel_ratio |
|---|---:|---:|---:|---:|---:|---:|
| 58 自然语言 | .911 | .974 | .911 | .974 | .837 | .983 |
| hard50 | .770 | .790 | .870 | .910 | .803 | .960 |
| 24 字面串 | — | — | .875 | — | — | — |

- 延迟 p50 ≈ 1.22s（含云端重排）。
- 答案质量（58 案）：Faithfulness `4.879`、Correctness `3.517`（1–5）；次级检查，不声明答案层优势。
- 单轮收紧复核：58 页/金标 `.9195/.9741`；hard50 页 `.780/.790`、金标 `.900/.910`（与上表不是同批）。

## 4. 核心实验

这里只保留六个真正改变设计或上线决策的实验。完整的逐轮日志、题集和结果不公开。

| # | 实验 | 解决的问题 | 改动 | 效果 | 决策 |
|---|---|---|---|---|---|
| 1 | 路由修复：fast 空结果 + gate 间接指代 | 间接问句在 fast 静默返回空；gate 把“那份/那家/某”误判为 direct，RAG 被绕过 | fast 空结果自动升级 balanced；补间接文档/组织指代规则 | fast 间接型 @5 `77% → 88%`；gate `83% → 100%`；端到端 @5 `70% → 87%` | 保留 |
| 2 | 多实体覆盖 | 比较类问句只检索到一侧实体，另一侧完全缺席 | 每实体独立向量腿；缺席实体页做受限 section 注入 | multi @10 `62% → 100%`；composite `67% → 92%`；总体 @10 `93% → 96%`；延迟约 `+0.4s` | 保留 |
| 3 | 词法条件化 C2 | 词法腿对自然语言有负贡献，但字面串/编号必须靠词法 | 只有 identifier 查询才启用词法过滤；其余查询走纯向量 | 58 gold@5 `.876 → .911`、gold@10 `.957 → .974`、入选 `.922 → .957`；hard50 gold@5 `.850 → .870`、gold@10 `.900 → .910`；字面串 `21/21`；lex 触发率 `14% / 6%` | 采用 C2 |
| 4 | E6-A 上下文预算 | 字符预算先到，出现“已入选但被截断” | 装入顺序从 score 改为 density（score / 块长） | 入选 `91.4% → 95.7%（+4.3pp）`；SHR `96.4% → 100%`；零回归；无额外延迟 | 采用 E6-A；E6-B/C 归档 |
| 5 | C2 × E6-A 组合 | 检索层词法条件化与组装层密度序是否互补 | 两项同时启用；按预注册护栏检查 X01/X04/X06 | 58 `.911 / .974 / .983`；hard50 page `.770 / .790`、gold `.870 / .910`、入选 `.960`；字面串 `21/21`；三项护栏全保 | 推荐生产配置 |
| 6 | 生产化效率：候选/上下文 + QMD 合并 | 候选和 18k 上下文偏大；一次 RAG 多次调用 QMD | balanced 20 / deep 16 + balanced context 12k；graph/source 扩展合并为一次 QMD 调用 | 候选：58 @5 `.9109 → .9195`、hard50 @5 `.77 → .78`，@10/入选不降，context 字符 `−22% / −10%`；QMD：调用 `2.33 → 1.83`（58）、`2.50 → 2.00`（hard50），页/金标/入选/MRR 逐位一致 | 采用；QMD 合并可回滚 `merge_restricted_expansions: false` |

> 多实体效果的归因：主召回来自 QMD 阶段的“每实体一条 `vec` 腿”；重排后的“缺席实体页受限 section”是只在实体页仍未进入候选/已选集合时才触发的兜底，不是主路径。当前 `62% → 100%` 是整包效果，未单独拆分两者的贡献。

补充证据（不单独占主线）：

- **组件消融**：full `87/93/91%`；no_rerank `71/80/76%`；lex_only `54/58/58%`；vec_only `94/98/97%`（page@5/@10/入选）。这组结果解释了为什么保留 rerank、以及为什么把 lex 条件化。
- **被拒绝的方案**：HyDE 58 @10 `−1.7pp`、H41 完全丢失、延迟约 `+1s`；E4-A 108 条查询零增益、`+0.4s`；reserve/aug 双集合全零；折扣 x1.30 @5 `+1.8pp` 但 @10 `−1.7pp`、2 条回归。全部不进入默认配置。
- **E0 方差基线**：control 3 轮逐位一致（page@5 `87.6%`、page@10 `95.7%`、入选 `91.4%`），说明本机管线近确定性，实验按 OFAT + 多轮复读判读。

## 5. 复现

```bash
# 检索评测（使用自己的 JSONL）
python bench/run_retrieval_eval.py --file bench/dataset/queries.jsonl --mode auto

# 组件消融
python bench/run_ablation.py --sets 58 --config config/rag.yaml

# 词法策略与 C2 分析
python bench/run_lex_ab.py --policy C2 --sets 58,h50,literal --repeat 3
python bench/analyze_lex_ab.py

# E6 预算实验
python bench/run_experiments.py run e6 A --repeat 3
python bench/analyze_e6.py

# 候选/上下文成本与 QMD 合并
python bench/sweep_pool_ctx.py
python bench/exp_qmd_merge.py --set 58 --variants baseline,merged
python bench/samebatch_merge.py
```

`bench/results/` 是运行产物，默认不提交。

## 6. 局限

- 单语料、小样本（58 / 50 / 19 / 24）；结果不外推到其他企业语料。
- 公开仓没有真实题集/金标，不能把本文件数字当作开箱即跑的基准。
- 答案质量是 LLM 裁判（同族模型、±1 噪声），不是人类金标。
- 指标只描述检索/组装层；不包含任何外部基线或跨架构对照。
