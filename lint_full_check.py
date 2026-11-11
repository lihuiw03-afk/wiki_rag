#!/usr/bin/env python3
"""Full Lint health check for company wiki - FINAL version with proper YAML parsing."""

import os
import re
import json
import hashlib
from datetime import datetime
from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parent
WIKI_DIR = WIKI_ROOT / "wiki"
RAW_DIR = WIKI_ROOT / "raw"
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH = WIKI_ROOT / "log.md"
HASHES_PATH = WIKI_ROOT / ".hashes.json"


# --- #UXXXX 导出安全文件名解码 + 路径归一化（与 .scripts/check_pending.py 同规则）---
_U_ESCAPE = re.compile(r"#U([0-9A-Fa-f]{4})")


def _norm_key(key):
    k = str(key).replace("\\", "/")
    if k.startswith("/home/demo/company-wiki/"):
        k = k[len("/home/demo/company-wiki/"):]
    elif k.startswith("/") or (len(k) > 1 and k[1] == ":"):
        try:
            from pathlib import Path as _P
            k = _P(k).resolve().relative_to(WIKI_ROOT.resolve()).as_posix()
        except Exception:
            pass
    return _U_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), k.lstrip("./"))

TODAY = datetime.now()
STALE_THRESHOLD = 90

VALID_TAGS = {
    "interview", "report", "industry-analysis", "competitor-analysis", "case-study", "proposal",
    "brand", "culture", "strategy", "execution", "organization", "market",
    "methodology", "framework", "model", "benchmark",
    "insight", "trend", "prediction", "risk", "opportunity",
    "comparison", "synthesis", "overview", "pitfall", "reference",
}

REQUIRED_FM_FIELDS = ["title", "created", "updated", "type", "tags", "sources", "compiled_by"]

def parse_frontmatter(text):
    """Parse YAML frontmatter, handling both inline and multi-line lists."""
    fm = {}
    if not text.startswith('---'):
        return fm
    lines = text.split('\n')
    if len(lines) < 2:
        return fm
    try:
        end = lines.index('---', 1)
    except ValueError:
        return fm
    
    fm_lines = lines[1:end]
    current_key = None
    current_list = None
    
    for line in fm_lines:
        # Check if this is a list item (starts with "  - " or "- ")
        if current_key and re.match(r'^\s+-\s+', line):
            item = re.sub(r'^\s+-\s+', '', line).strip()
            if current_list is None:
                current_list = []
            current_list.append(item)
            fm[current_key] = current_list
            continue
        
        # Check if this is a new key: value line
        if ':' in line and not line.startswith(' ') and not line.startswith('-'):
            key = line.split(':', 1)[0].strip()
            val = line.split(':', 1)[1].strip()
            current_key = key
            current_list = None
            
            if not val:
                # Value might be a multi-line list following
                fm[key] = ''
                continue
            elif val.startswith('[') and val.endswith(']'):
                # Inline list
                items = [v.strip().strip("'\"") for v in val[1:-1].split(',') if v.strip()]
                fm[key] = items
            else:
                fm[key] = val.strip("'\"")
    
    return fm

def extract_wikilinks(content):
    """Extract wikilinks, excluding those inside backticks."""
    # Remove inline code blocks first
    cleaned = re.sub(r'`[^`]+`', '', content)
    # Now extract wikilinks
    raw_links = re.findall(r'\[\[([^\]]+)\]\]', cleaned)
    wikilinks = []
    for link in raw_links:
        target = link.split('|')[0].split('#')[0].strip()
        if target:
            wikilinks.append(target)
    return wikilinks

def parse_md(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    fm = parse_frontmatter(content)
    wikilinks = extract_wikilinks(content)
    lines = len(content.split('\n'))
    return fm, wikilinks, lines, content

# Collect all wiki .md files
wiki_files = []
for root, dirs, files in os.walk(WIKI_DIR):
    for f in files:
        if f.endswith(".md"):
            wiki_files.append(Path(root) / f)

# Parse all pages
pages_data = {}
infra_files = {"index.md", "log.md"}

for wf in wiki_files:
    rel = wf.relative_to(WIKI_DIR)
    rel_str = str(rel)
    if rel_str in infra_files:
        continue
    slug = str(rel.with_suffix(''))
    short_name = wf.stem
    fm, links, line_count, content = parse_md(wf)
    pages_data[short_name] = {
        'path': wf,
        'slug': slug,
        'short_name': short_name,
        'fm': fm,
        'wikilinks': links,
        'lines': line_count,
        'content': content,
    }

valid_targets = set(pages_data.keys())

# === RESULTS ===
results = []
results.append("=" * 70)
results.append("企业知识库 Lint 健康检查报告")
results.append(f"检查时间: {TODAY.strftime('%Y-%m-%d')}")
results.append(f"页面总数: {len(pages_data)} (内容页)")
results.append("=" * 70)

# === 1. Dead links ===
dead_links = {}
for name, data in pages_data.items():
    for link in data['wikilinks']:
        if link not in valid_targets:
            if name not in dead_links:
                dead_links[name] = []
            if link not in dead_links[name]:
                dead_links[name].append(link)

results.append(f"\n{'─' * 70}")
results.append("[严重] 1. 死链 (Dead Links)")
results.append(f"{'─' * 70}")
if dead_links:
    total_dead = sum(len(v) for v in dead_links.values())
    results.append(f"  发现 {len(dead_links)} 个页面包含 {total_dead} 条死链:")
    for name, links in sorted(dead_links.items()):
        data = pages_data[name]
        results.append(f"  wiki/{data['slug']}.md:")
        for l in links:
            results.append(f"    - [[{l}]]")
else:
    results.append("  ✓ 无死链")

# === 2. Orphan pages ===
inlinks = {name: 0 for name in pages_data}
for name, data in pages_data.items():
    for link in data['wikilinks']:
        if link in inlinks:
            inlinks[link] += 1

orphans = [name for name, count in inlinks.items() if count == 0]

results.append(f"\n{'─' * 70}")
results.append("[严重] 2. 孤儿页 (Orphan Pages - 零入链)")
results.append(f"{'─' * 70}")
if orphans:
    results.append(f"  发现 {len(orphans)} 个零入链页面:")
    for name in sorted(orphans):
        data = pages_data[name]
        results.append(f"  - wiki/{data['slug']}.md")
else:
    results.append("  ✓ 无孤儿页")

# === 3. Index completeness ===
with open(INDEX_PATH, 'r') as f:
    index_content = f.read()
index_links_raw = re.findall(r'\[\[([^\]]+)\]\]', index_content)
index_targets = set()
for link in index_links_raw:
    target = link.split('|')[0].split('#')[0].strip()
    index_targets.add(target)

missing_from_index = []
for name in pages_data:
    if name not in index_targets:
        missing_from_index.append(name)

results.append(f"\n{'─' * 70}")
results.append("[严重] 3. 索引完整性 (Index Completeness)")
results.append(f"{'─' * 70}")
if missing_from_index:
    results.append(f"  发现 {len(missing_from_index)} 个页面未列入 index.md:")
    for name in sorted(missing_from_index):
        data = pages_data[name]
        results.append(f"  - wiki/{data['slug']}.md")
else:
    results.append("  ✓ 所有页面已列入 index.md")

# === 4. Frontmatter validation ===
fm_issues = {}
for name, data in pages_data.items():
    issues = []
    fm = data['fm']
    for field in REQUIRED_FM_FIELDS:
        val = fm.get(field, '')
        if not val or val == '' or val == []:
            issues.append(f"missing '{field}'")
    if 'type' in fm and fm['type']:
        valid_types = ['entity', 'concept', 'source', 'synthesis', 'finding', 'comparison']
        if fm['type'] not in valid_types:
            issues.append(f"invalid type '{fm['type']}'")
    if issues:
        fm_issues[name] = issues

results.append(f"\n{'─' * 70}")
results.append("[中等] 4. Frontmatter 校验")
results.append(f"{'─' * 70}")
if fm_issues:
    results.append(f"  发现 {len(fm_issues)} 个页面有 Frontmatter 问题:")
    for name, issues in sorted(fm_issues.items()):
        data = pages_data[name]
        results.append(f"  wiki/{data['slug']}.md: {', '.join(issues)}")
else:
    results.append("  ✓ 所有页面 Frontmatter 合规")

# === 5. Tag audit ===
invalid_tags = {}
all_tags_used = set()
for name, data in pages_data.items():
    fm = data['fm']
    tags = fm.get('tags', [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(',') if t.strip()]
    for tag in tags:
        tag = tag.strip()
        if tag:
            all_tags_used.add(tag)
            if tag not in VALID_TAGS:
                if name not in invalid_tags:
                    invalid_tags[name] = []
                if tag not in invalid_tags[name]:
                    invalid_tags[name].append(tag)

results.append(f"\n{'─' * 70}")
results.append("[中等] 5. 标签审计 (Tag Audit)")
results.append(f"{'─' * 70}")
if invalid_tags:
    results.append(f"  发现 {len(invalid_tags)} 个页面使用非法标签:")
    for name, tags in sorted(invalid_tags.items()):
        data = pages_data[name]
        results.append(f"  wiki/{data['slug']}.md: {tags}")
else:
    results.append("  ✓ 所有标签均在 SCHEMA 分类体系中")
results.append(f"  当前使用的所有标签: {sorted(all_tags_used)}")

# === 6. Source file drift ===
with open(HASHES_PATH, 'r', encoding='utf-8') as f:
    hashes = json.load(f)

disk_map = {}
for _root, _dirs, _files in os.walk(RAW_DIR):
    for _n in _files:
        _p = Path(_root) / _n
        disk_map[_norm_key(_p.relative_to(WIKI_ROOT).as_posix())] = _p

drift_files = []
for stored_path, stored_hash in hashes.items():
    full = disk_map.get(_norm_key(str(stored_path)))
    if full is None:
        drift_files.append((stored_path, "FILE MISSING"))
        continue
    try:
        with open(full, 'rb') as f:
            current_hash = hashlib.sha256(f.read()).hexdigest()
        if current_hash != stored_hash:
            drift_files.append((stored_path, "hash mismatch"))
    except Exception as e:
        drift_files.append((stored_path, f"error: {e}"))

results.append(f"\n{'─' * 70}")
results.append("[中等] 6. 源文件漂移 (Source File Drift)")
results.append(f"{'─' * 70}")
if drift_files:
    results.append(f"  发现 {len(drift_files)} 个文件异常:")
    for path, issue in drift_files:
        results.append(f"  {path} → {issue}")
else:
    results.append("  ✓ 所有源文件 sha256 匹配")

# === 7. Contested pages ===
contested_pages = []
for name, data in pages_data.items():
    fm = data['fm']
    if str(fm.get('contested', '')).lower() == 'true':
        contested_pages.append(name)

results.append(f"\n{'─' * 70}")
results.append("[低] 7. 矛盾标记 (Contested Pages)")
results.append(f"{'─' * 70}")
if contested_pages:
    for name in contested_pages:
        data = pages_data[name]
        results.append(f"  - wiki/{data['slug']}.md (contested: true)")
else:
    results.append("  ✓ 无 contested 页面")

# === 8. Stale content ===
stale_pages = {}
for name, data in pages_data.items():
    fm = data['fm']
    updated = fm.get('updated', '')
    if updated and isinstance(updated, str):
        try:
            upd_date = datetime.strptime(updated, '%Y-%m-%d')
            days_ago = (TODAY - upd_date).days
            if days_ago > STALE_THRESHOLD:
                stale_pages[name] = days_ago
        except ValueError:
            pass

results.append(f"\n{'─' * 70}")
results.append(f"[低] 8. 过时内容 (Stale Content, >{STALE_THRESHOLD}天)")
results.append(f"{'─' * 70}")
if stale_pages:
    for name, days in sorted(stale_pages.items(), key=lambda x: -x[1]):
        data = pages_data[name]
        results.append(f"  - wiki/{data['slug']}.md (updated: {data['fm'].get('updated')}, {days}天前)")
else:
    results.append("  ✓ 无过时内容")

# === 9. Quality signals ===
low_confidence = []
single_source = []
multi_source_count = 0
for name, data in pages_data.items():
    fm = data['fm']
    conf = fm.get('confidence', '')
    if conf == 'low':
        low_confidence.append(name)
    sources = fm.get('sources', [])
    if isinstance(sources, str):
        if sources:
            sources = [sources]
        else:
            sources = []
    # Filter out empty strings
    sources = [s for s in sources if s]
    if len(sources) <= 1:
        single_source.append((name, sources))
    else:
        multi_source_count += 1

results.append(f"\n{'─' * 70}")
results.append("[低] 9. 质量信号 (Quality Signals)")
results.append(f"{'─' * 70}")
if low_confidence:
    results.append(f"  confidence: low 的页面 ({len(low_confidence)}个):")
    for name in sorted(low_confidence):
        data = pages_data[name]
        results.append(f"    - wiki/{data['slug']}.md")
else:
    results.append("  ✓ 无 confidence: low 页面")

# Only report truly single-source (has exactly 1 source) or zero-source
zero_source = [(n, s) for n, s in single_source if len(s) == 0]
one_source = [(n, s) for n, s in single_source if len(s) == 1]

results.append(f"  多来源页面: {multi_source_count}个 ✓")
if one_source:
    results.append(f"  单来源页面: {len(one_source)}个")
    for name, src in sorted(one_source):
        data = pages_data[name]
        results.append(f"    - wiki/{data['slug']}.md → {src[0]}")
if zero_source:
    results.append(f"  零来源页面 (sources为空): {len(zero_source)}个")
    for name, src in sorted(zero_source):
        data = pages_data[name]
        results.append(f"    - wiki/{data['slug']}.md")

# === 10. Oversized pages ===
oversized = []
for name, data in pages_data.items():
    if data['lines'] > 200:
        oversized.append((name, data['lines']))

results.append(f"\n{'─' * 70}")
results.append("[低] 10. 页面过大 (>200行)")
results.append(f"{'─' * 70}")
if oversized:
    for name, lines in sorted(oversized, key=lambda x: -x[1]):
        data = pages_data[name]
        results.append(f"  - wiki/{data['slug']}.md ({lines}行)")
else:
    results.append("  ✓ 无超大页面")

# === 11. Log rotation ===
with open(LOG_PATH, 'r') as f:
    log_content = f.read()
log_entries = len(re.findall(r'^## \[', log_content, re.MULTILINE))

results.append(f"\n{'─' * 70}")
results.append("[信息] 11. 日志轮转 (Log Rotation)")
results.append(f"{'─' * 70}")
results.append(f"  log.md 条目数: {log_entries}/500")
if log_entries >= 500:
    results.append(f"  ⚠ 需要归档！建议重命名为 log-{TODAY.year}.md 并新建 log.md")
else:
    results.append(f"  ✓ 无需轮转 (剩余 {500 - log_entries} 条)")

# === SUMMARY ===
results.append(f"\n{'=' * 70}")
results.append("修复建议汇总 (按严重程度排序)")
results.append(f"{'=' * 70}")

suggestions = []
if dead_links:
    unique_dead = set()
    for links in dead_links.values():
        unique_dead.update(links)
    suggestions.append(f"[HIGH] 死链: {len(unique_dead)} 个不存在的目标 → 创建页面或移除链接: {sorted(unique_dead)}")
if orphans:
    # Filter out overview.md (infrastructure)
    real_orphans = [o for o in orphans if o != 'overview']
    if real_orphans:
        suggestions.append(f"[HIGH] 孤儿页: {len(real_orphans)} 个零入链内容页 → 在相关页面中添加反向链接")
    else:
        suggestions.append(f"[INFO] 孤儿页: overview.md 零入链（基础设施页面，可忽略）")
if missing_from_index:
    real_missing = [m for m in missing_from_index if m != 'overview']
    if real_missing:
        suggestions.append(f"[HIGH] 索引缺失: {len(real_missing)} 个页面未列入 index.md")
    else:
        suggestions.append(f"[INFO] 索引: overview.md 未以 wikilink 形式列入 index.md（已在分类标题中提及）")
if fm_issues:
    suggestions.append(f"[MEDIUM] Frontmatter: {len(fm_issues)} 个页面缺少必填字段（主要是 sources 字段为空）→ 下次编译时补充")
if invalid_tags:
    all_invalid = set()
    for tags in invalid_tags.values():
        all_invalid.update(tags)
    suggestions.append(f"[LOW] 非法标签: {sorted(all_invalid)} → 建议将 consulting/diagnosis/digital/energy/manufacturing 加入 SCHEMA.md 标签体系，或替换为已有标签")
if zero_source:
    suggestions.append(f"[LOW] 零来源: {len(zero_source)} 个页面 sources 字段为空 → 编译时补充来源追溯")
if low_confidence:
    suggestions.append(f"[LOW] 低置信度: {len(low_confidence)} 个页面 confidence:low → 需补充更多来源支撑")
if oversized:
    for name, lines in oversized:
        data = pages_data[name]
        suggestions.append(f"[LOW] 页面过大: wiki/{data['slug']}.md ({lines}行) → 考虑拆分")

for i, s in enumerate(suggestions, 1):
    results.append(f"  {i}. {s}")

results.append(f"\n{'=' * 70}")
results.append("检查完成")
results.append(f"{'=' * 70}")

# Print report
report = '\n'.join(results)
print(report)

# Save report for reference（跨平台：系统临时目录，UTF-8 + LF）
try:
    import tempfile
    _report_path = Path(tempfile.gettempdir()) / "lint_report.txt"
    with open(_report_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(report)
    print(f"[参考] 完整报告已保存: {_report_path}")
except OSError:
    pass
