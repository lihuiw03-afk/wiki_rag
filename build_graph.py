#!/usr/bin/env python3
"""Build knowledge graph report from wiki markdown files."""

import re
import os
from collections import defaultdict
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
wiki_dir = ROOT / "wiki"

# Collect all .md files
all_md_files = sorted(wiki_dir.rglob("*.md"))

# Parse each file for [[wikilinks]] and tags
wikilink_pattern = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')
tag_yaml_pattern = re.compile(r'^tags:\s*\[([^\]]+)\]', re.MULTILINE)

nodes = set()
edges = []  # (source_stem, target_stem)
out_degree = defaultdict(int)
in_degree = defaultdict(int)
node_tags = defaultdict(list)
node_categories = {}

for f in all_md_files:
    stem = f.stem
    if stem in ('graph-report', 'log'):
        continue
    nodes.add(stem)
    
    # Determine category from path
    rel = f.relative_to(wiki_dir)
    if len(rel.parts) > 1:
        node_categories[stem] = rel.parts[0]
    else:
        node_categories[stem] = 'root'
    
    content = f.read_text(encoding='utf-8')
    
    # Extract tags from YAML frontmatter
    tag_match = tag_yaml_pattern.search(content)
    if tag_match:
        tags_raw = tag_match.group(1)
        tags = [t.strip().strip('"').strip("'") for t in tags_raw.split(',')]
        node_tags[stem] = [t for t in tags if t]
    
    # Extract wikilinks
    links = wikilink_pattern.findall(content)
    for link in links:
        link = link.strip()
        # Remove trailing backslashes (markdown escape artifacts)
        link = link.rstrip('\\')
        # Remove section anchors (#section) for node resolution
        base_link = link.split('#')[0]
        edges.append((stem, base_link))
        out_degree[stem] += 1
        in_degree[base_link] += 1

# Statistics
total_nodes = len(nodes)
total_edges = len(edges)
avg_out_degree = total_edges / total_nodes if total_nodes > 0 else 0

# Top 10 most referenced (by in-degree)
in_degree_sorted = sorted(in_degree.items(), key=lambda x: x[1], reverse=True)
top10_referenced = in_degree_sorted[:10]

# Orphan pages: nodes with zero in-degree (never referenced by others)
orphan_pages = []
for n in nodes:
    if in_degree.get(n, 0) == 0:
        orphan_pages.append(n)
orphan_pages.sort()

# Knowledge domain clustering by tags
tag_clusters = defaultdict(list)
for stem, tags in node_tags.items():
    for tag in tags:
        tag_clusters[tag].append(stem)

tag_clusters_sorted = sorted(tag_clusters.items(), key=lambda x: len(x[1]), reverse=True)

# Dangling links (links pointing to non-existent pages)
dangling_links = set()
for src, tgt in edges:
    if tgt not in nodes:
        dangling_links.add(tgt)

# Category distribution
cat_counts = defaultdict(int)
for stem, cat in node_categories.items():
    cat_counts[cat] += 1

# Dangling links (links pointing to non-existent pages)
dangling_links = defaultdict(list)  # target -> list of sources
for src, tgt in edges:
    if tgt not in nodes:
        dangling_links[tgt].append(src)

# Build report
report_lines = []
report_lines.append("# 知识图谱统计报告")
report_lines.append("")
report_lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
report_lines.append("")
report_lines.append("## 统计摘要")
report_lines.append("")
report_lines.append("| 指标 | 数值 |")
report_lines.append("|------|------|")
report_lines.append(f"| 总节点数（页面） | {total_nodes} |")
report_lines.append(f"| 总边数（引用关系） | {total_edges} |")
report_lines.append(f"| 平均出度 | {avg_out_degree:.2f} |")
report_lines.append(f"| 有入链的页面数 | {len([n for n in nodes if in_degree.get(n, 0) > 0])} |")
report_lines.append(f"| 孤立页面数（零入链） | {len(orphan_pages)} |")
report_lines.append(f"| 悬空链接数（指向不存在页面） | {len(dangling_links)} |")
report_lines.append("")

# Category distribution
report_lines.append("### 分类分布")
report_lines.append("")
report_lines.append("| 分类 | 页面数 |")
report_lines.append("|------|--------|")
for cat, count in sorted(cat_counts.items(), key=lambda x: x[1], reverse=True):
    report_lines.append(f"| {cat} | {count} |")
report_lines.append("")

# Top 10 most referenced
report_lines.append("## Top 10 被引用最多的页面（关键枢纽）")
report_lines.append("")
report_lines.append("| 排名 | 页面 | 入链数 | 分类 |")
report_lines.append("|------|------|--------|------|")
for i, (page, count) in enumerate(top10_referenced, 1):
    cat = node_categories.get(page, '—')
    exists = "✓" if page in nodes else "✗ (悬空)"
    report_lines.append(f"| {i} | [[{page}]] | {count} | {cat} {exists} |")
report_lines.append("")

# Top 10 orphan pages
report_lines.append("## Top 10 孤立页面（零入链）")
report_lines.append("")
report_lines.append("以下页面没有任何入链，可考虑补充交叉链接。")
report_lines.append("")
report_lines.append("| 序号 | 页面 | 分类 | 出度 |")
report_lines.append("|------|------|------|------|")
for i, page in enumerate(orphan_pages[:10], 1):
    cat = node_categories.get(page, '—')
    od = out_degree.get(page, 0)
    report_lines.append(f"| {i} | [[{page}]] | {cat} | {od} |")
report_lines.append("")
if len(orphan_pages) > 10:
    report_lines.append(f"*（共 {len(orphan_pages)} 个孤立页面，此处列出前 10 个）*")
    report_lines.append("")

# Knowledge domain clustering
report_lines.append("## 知识域聚类（按 Tag 分组）")
report_lines.append("")
for tag, members in tag_clusters_sorted:
    report_lines.append(f"### `{tag}` ({len(members)} 页)")
    report_lines.append("")
    for m in sorted(members):
        cat = node_categories.get(m, '—')
        report_lines.append(f"- [[{m}]] _({cat})_")
    report_lines.append("")

# Dangling links
if dangling_links:
    report_lines.append("## 悬空链接（指向不存在的页面）")
    report_lines.append("")
    report_lines.append("以下链接没有对应页面，请检查或补充。")
    report_lines.append("")
    report_lines.append("| 链接目标 | 被引用次数 | 引用来源 |")
    report_lines.append("|----------|------------|----------|")
    for tgt, sources in sorted(dangling_links.items(), key=lambda x: len(x[1]), reverse=True):
        src_list = ", ".join(f"[[{s}]]" for s in sorted(set(sources)))
        report_lines.append(f"| [[{tgt}]] | {len(sources)} | {src_list} |")
    report_lines.append("")

# Write report
report_path = wiki_dir / "graph-report.md"
report_path.write_text("\n".join(report_lines), encoding='utf-8')
print(f"Report written to {report_path}")
print(f"Nodes: {total_nodes}, Edges: {total_edges}, Avg out-degree: {avg_out_degree:.2f}")
print(f"Orphan pages: {len(orphan_pages)}, Dangling links: {len(dangling_links)}")
