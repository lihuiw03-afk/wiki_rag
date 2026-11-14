# BYO-Corpus：用自己的语料跑评测

公开仓**不包含任何真实语料**。本文件说明如何把 `bench/` 接到你自己的知识库上，
复现与 README 同口径的页覆盖、金标覆盖、MRR、入选率与 SHR 评测。

## 1. 需要准备什么

| 依赖 | 说明 |
|---|---|
| `rag/manifest.json` | 编译产物：节点清单（node_id、raw_path、page_path、type、section 等） |
| `wiki/` | 编译后的知识库页面（markdown + frontmatter） |
| `rag/qmd/` | QMD 投影（core / concept / source / evidence 集合） |
| qmd daemon | `127.0.0.1:8181`，索引指向本项目（`scripts/qmd_daemon.sh`） |
| `config/rag.yaml` | 检索配置（模式、集合、rerank、lex_leg、budget_policy） |

最小验证：

```bash
python scripts/rag_status.py --json          # 索引/节点数/健康状态
python scripts/rag_search.py "你的问题" --mode balanced --json
```

## 2. 评测集格式（JSONL，每行一个 case）

```json
{
  "id": "E01",
  "queries": [
    {"text": "示例公司A的文化建设面临哪些问题？", "style": "direct"},
    {"text": "那家示例公司的文化项目主要解决什么？", "style": "indirect"}
  ],
  "target_pages": ["wiki/entity/示例公司A.md"],
  "target_raws": ["raw/20260622_demo_访谈整理.md"],
  "notes": "optional"
}
```

字段约定：

- `queries[].style`：`direct` / `indirect` / `multi` / `composite` 等，用于分组统计；
- `target_pages`：**页口径**金标（wiki 相对路径）；
- `target_raws`：**金标口径**金标（原始文档相对路径；evidence 类查询必须有）；
- 一个 case 可有多条 query（同一目标的直接/间接问法），评测按 query 展开。

## 3. 金标怎么标

1. 先确定“回答这个问题必须读到哪一页/哪一份原件”；
2. 页面级问题 → `target_pages`；原始证据/访谈/表格类 → 同时给 `target_raws`；
3. 不要用“检索结果里有什么”反推金标（会污染评测）；
4. 保留一部分查询作为 **holdout**，不用于调参（字面串集按 dev/test 拆分）。

## 4. 运行

```bash
# 检索评测（使用你自己的 JSONL）
python bench/run_retrieval_eval.py --file bench/dataset/queries.jsonl --mode auto

# 组件消融（full / no_rerank / no_augment / lex_only / vec_only）
python bench/run_ablation.py --sets 58 --config config/rag.yaml

# 词法策略对照（on/off/conditional/C2）
python bench/run_lex_ab.py --policy C2 --sets 58 --repeat 3
python bench/analyze_lex_ab.py

# 可选答案质量抽查（需要仓库外的模型 Key）
python bench/judge.py --sets 58
```

指标定义见 `docs/EVALUATION.md`；注意 **页口径与金标口径不可混用**。

## 5. 合成演示语料（推荐用于 CI/演示）

如果你暂时没有自己的语料，可以先生成一个完全虚构的小语料：
- 2–3 家虚构公司、若干人物/项目/制度文档；
- 覆盖 entity / concept / source / evidence 四类节点；
- 同步生成 direct / indirect / multi / literal 查询与金标；
- 产出可直接被 `qmd` 索引的最小语料。

合成语料只用于验证管线可运行；其指标不可与真实语料数字横向比较。

## 6. 隐私

- 不要把 `raw/ wiki/ rag/ bench/dataset/ bench/results/` 提交到公开仓（`.gitignore` 已阻断）；
- 评测结果对外只发聚合指标，不发查询原文/回答/金标路径；
- 分享 trace 前先脱敏（实体、路径、时间、项目代号）。
