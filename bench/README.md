# bench

评测执行器，不包含题集、金标或结果文件。`bench/dataset/` 与 `bench/results/` 由使用者按
[../docs/BYO_CORPUS.md](../docs/BYO_CORPUS.md) 自备，且不提交。

| 任务 | 脚本 |
|---|---|
| 检索评测（58 / hard50 / 19 / 24） | `run_retrieval_eval.py` |
| 组件消融（full / no_rerank / lex_only / vec_only） | `run_ablation.py` |
| 词法策略 A/B/C/C2 | `run_lex_ab.py`、`lex_routing.py`、`analyze_lex_ab.py` |
| E6-A 上下文预算 | `run_experiments.py`、`analyze_e6.py` |
| 候选/上下文成本扫描 | `sweep_pool_ctx.py` |
| QMD 扩展调用合并 | `exp_qmd_merge.py`、`samebatch_merge.py` |

指标定义见 [../docs/EVALUATION.md](../docs/EVALUATION.md)。只保留这六个核心实验的执行/分析代码；
其它一次性诊断、数据构造和报告不随公开仓发布。
