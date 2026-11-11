# Design Decisions

## DD-01: 不替换 LLM-Wiki

RAG 是“查知识”的机制，LLM-Wiki 是“生产/维护知识”的机制。二者职责不同。

## DD-02: 不引入 ChromaDB

QMD 已经使用 SQLite + sqlite-vec。当前规模下再加入 ChromaDB 会产生双向量索引、双生命周期和额外服务，没有收益。

## DD-03: Projection 而非修改 Wiki

检索优化字段、breadcrumb、章节预览和关键词全部进入 `rag/`。Wiki 保持面向人的知识资产形态。

## DD-04: Structure 只做后置、预算化扩展

结构节点不是“越多越好”。只有 QMD 高分命中才允许 Parent/Child、Graph、Source Evidence 扩展，并设置严格预算。

## DD-05: Graph Neighbor 必须选相关 Section

Wikilink 只证明“页面相关”，不证明该页 root 就是当前问题需要的内容。因此 Graph Expansion 采用：

`neighbor page → restricted QMD retrieval → best section`。

若目标页没有进入 QMD 返回窗口，用轻量本地 scorer 兜底，而不是退回盲目 root 注入。

## DD-06: Provenance 不等于 Evidence

Wiki `raw_sources` 只说明来源关系。真正用于回答的问题证据应该根据当前 Query 在关联 raw source 中重新选择，因此不再默认取第一个 chunk。

## DD-07: Wiki + Evidence 双索引

Wiki 页面是 LLM 编译后的高密度知识，天然有信息压缩；raw evidence 保留细节。两者一起索引，同时保留知识密度与原始细节。

## DD-08: Context Diversity 必须自适应

固定“每个 source 最多 2 chunk”会伤害单文档深挖的完整性。因此 Context Builder 区分：

- diversity：强来源多样性
- balanced：一般问答
- source-focused：允许同源证据占主导并补邻接 chunk

## DD-09: 当前所有 Profile 全权限

业务前提是所有员工都能访问完整 Company Wiki，因此不加入 ACL/RBAC 复杂度。Profile 只保留为 trace caller identity。

## DD-10: HyDE 自适应

精确术语、人名、编号默认禁用 HyDE；抽象综合问题由 Hermes 显式提供 HyDE 文本，降低 query drift 与额外生成成本。

## V2.4 — Why visual evidence is textified before QMD

QMD remains the retrieval kernel; it does not need a separate image-vector database in this version. Visual binaries are first interpreted into two textual representations:

1. OCR text for exact lexical recall;
2. VLM semantic description for diagram/layout/relationship recall.

Both are indexed by the same FTS5 + vector + rerank pipeline as normal evidence. This keeps one retrieval stack and lets a query such as “条件不成立时走哪一步” retrieve a flowchart even when the original source contains no selectable text.

The original image/page render is retained under `rag/assets/` with SHA and source locator, so the transformed visual text is auditable and can be regenerated with a better OCR/VLM later.

## V2.4 — Why not run full Vision synchronously on every upload

Full visual analysis is intentionally split by lifecycle:

- Hot path: bounded materialization/rendering only; visual reasoning is delegated to Hermes native Vision for the current turn, so upload-to-answer latency never depends on the background API.
- Long-term projection: full configured multimodal pass before `qmd update/embed`.

This prevents a 100-slide presentation from blocking the employee's first question while still ensuring the permanent `company-evidence` corpus eventually contains visual semantics.


## V2.4 — Why split Hermes Vision from background Vision API

Hot queries optimize **time-to-first-answer** and already run inside Hermes, so Hermes native Vision is the lowest-friction visual reasoning path. Long-term evidence optimizes **repeatability, caching and indexability**, so it uses a configured external model API and persists the output as `visual_semantics`. Keeping them separate prevents API latency from blocking uploads and prevents transient session observations from silently becoming durable knowledge.
