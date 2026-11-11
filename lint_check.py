#!/usr/bin/env python3
"""
Company Wiki Lint Health Check - 11-dimension diagnostic.
"""

import os
import re
import json
import hashlib
from datetime import datetime
from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parent
WIKI_DIR = WIKI_ROOT / "wiki"
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
INFRA_PAGES = {"overview.md", "graph-report.md", "index.md", "log.md"}


def parse_frontmatter(text):
    fm = {}
    if not text.startswith('---'):
        return fm
    lines = text.split('\n')
    try:
        end = lines.index('---', 1)
    except ValueError:
        return fm

    current_key = None
    current_list = None

    for line in lines[1:end]:
        if current_key and re.match(r'^\s+-\s+', line):
            item = re.sub(r'^\s+-\s+', '', line).strip().strip("'\"")
            if current_list is None:
                current_list = []
            current_list.append(item)
            fm[current_key] = current_list
            continue
        if ':' in line and not line.startswith(' ') and not line.startswith('-'):
            key = line.split(':', 1)[0].strip()
            val = line.split(':', 1)[1].strip()
            current_key = key
            current_list = None
            if not val:
                fm[key] = ''
            elif val.startswith('[') and val.endswith(']'):
                fm[key] = [v.strip().strip("'\"") for v in val[1:-1].split(',') if v.strip()]
            else:
                fm[key] = val.strip("'\"")
    return fm


def extract_wikilinks(content):
    cleaned = re.sub(r'`[^`]+`', '', content)
    raw_links = re.findall(r'\[\[([^\]]+)\]\]', cleaned)
    result = []
    for link in raw_links:
        target = link.split('|')[0].split('#')[0].strip()
        if target:
            result.append(target)
    return result


def parse_md(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    fm = parse_frontmatter(content)
    wikilinks = extract_wikilinks(content)
    return fm, wikilinks, len(content.split('\n'))


def main():
    wiki_files = []
    for root, dirs, files in os.walk(WIKI_DIR):
        for f in files:
            if f.endswith(".md"):
                wiki_files.append(Path(root) / f)

    pages_data = {}
    for wf in wiki_files:
        rel = wf.relative_to(WIKI_DIR)
        if str(rel) in INFRA_PAGES:
            continue
        fm, links, lines = parse_md(wf)
        pages_data[wf.stem] = {
            'path': wf, 'slug': str(rel.with_suffix('')),
            'fm': fm, 'wikilinks': links, 'lines': lines,
        }

    valid_targets = set(pages_data.keys())
    issues = {"HIGH": [], "MEDIUM": [], "LOW": [], "INFO": []}

    # 1. Dead links
    dead = {}
    for name, data in pages_data.items():
        for link in data['wikilinks']:
            if link not in valid_targets:
                dead.setdefault(name, set()).add(link)
    if dead:
        unique = set()
        for s in dead.values():
            unique.update(s)
        issues["HIGH"].append(f"Dead links ({len(unique)} targets): {sorted(unique)}")
        for src, targets in sorted(dead.items()):
            issues["HIGH"].append(f"  {src} -> {sorted(targets)}")

    # 2. Orphans
    inlinks = {n: 0 for n in pages_data}
    for data in pages_data.values():
        for link in data['wikilinks']:
            if link in inlinks:
                inlinks[link] += 1
    orphans = [n for n, c in inlinks.items() if c == 0]
    if orphans:
        issues["HIGH"].append(f"Orphans ({len(orphans)}): {sorted(orphans)}")

    # 3. Index completeness
    with open(INDEX_PATH, 'r') as f:
        idx_content = f.read()
    idx_links = set(re.findall(r'\[\[([^\]|#]+)', idx_content))
    missing = [n for n in pages_data if n not in idx_links]
    if missing:
        issues["HIGH"].append(f"Not in index ({len(missing)}): {sorted(missing)}")

    # 4. Frontmatter
    fm_bad = {n: [f for f in REQUIRED_FM_FIELDS if not d['fm'].get(f)] for n, d in pages_data.items()}
    fm_bad = {n: v for n, v in fm_bad.items() if v}
    if fm_bad:
        issues["MEDIUM"].append(f"Frontmatter missing ({len(fm_bad)} pages):")
        for n, missing_fields in sorted(fm_bad.items()):
            issues["MEDIUM"].append(f"  {n}: missing {missing_fields}")

    # 5. Tags
    bad_tags = {}
    for name, data in pages_data.items():
        tags = data['fm'].get('tags', [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        for t in tags:
            if t and t not in VALID_TAGS:
                bad_tags.setdefault(t, []).append(name)
    if bad_tags:
        issues["MEDIUM"].append(f"Invalid tags ({len(bad_tags)}):")
        for tag, pages in sorted(bad_tags.items()):
            issues["MEDIUM"].append(f"  '{tag}' used in: {pages}")

    # 6. Source drift
    drift = []
    if HASHES_PATH.exists():
        with open(HASHES_PATH, 'r', encoding='utf-8') as f:
            hashes = json.load(f)
        disk_map = {}
        for _root, _dirs, _files in os.walk(WIKI_ROOT / "raw"):
            for _n in _files:
                _p = Path(_root) / _n
                disk_map[_norm_key(_p.relative_to(WIKI_ROOT).as_posix())] = _p
        for path, stored in hashes.items():
            full = disk_map.get(_norm_key(str(path)))
            if full is None:
                drift.append(f"{path} MISSING")
            else:
                with open(full, 'rb') as fh:
                    actual = hashlib.sha256(fh.read()).hexdigest()
                if actual != stored:
                    drift.append(f"{path} MISMATCH")
    if drift:
        issues["MEDIUM"].append(f"Source drift ({len(drift)}): {drift}")

    # 7. Contested
    contested = [n for n, d in pages_data.items() if str(d['fm'].get('contested', '')).lower() == 'true']
    if contested:
        issues["LOW"].append(f"Contested ({len(contested)}): {sorted(contested)}")

    # 8. Stale
    stale = {}
    for n, d in pages_data.items():
        upd = d['fm'].get('updated', '')
        if upd:
            try:
                days = (TODAY - datetime.strptime(upd, '%Y-%m-%d')).days
                if days > STALE_THRESHOLD:
                    stale[n] = days
            except ValueError:
                pass
    if stale:
        issues["LOW"].append(f"Stale >90d ({len(stale)}):")
        for n, days in sorted(stale.items(), key=lambda x: -x[1]):
            issues["LOW"].append(f"  {n}: {days} days")

    # 9. Quality
    low_conf = [n for n, d in pages_data.items() if d['fm'].get('confidence') == 'low']
    single_src = []
    for n, d in pages_data.items():
        sources = d['fm'].get('sources', [])
        if isinstance(sources, str):
            sources = [sources] if sources else []
        if len(sources) <= 1:
            single_src.append(n)
    if low_conf:
        issues["LOW"].append(f"confidence:low ({len(low_conf)}): {sorted(low_conf)}")
    issues["INFO"].append(f"Single-source pages: {len(single_src)}/{len(pages_data)}")
    if single_src:
        issues["INFO"].append(f"  {sorted(single_src)}")

    # 10. Oversized
    big = [(n, d['lines']) for n, d in pages_data.items() if d['lines'] > 200]
    if big:
        issues["LOW"].append(f"Oversized >200 lines ({len(big)}):")
        for n, lines in sorted(big, key=lambda x: -x[1]):
            issues["LOW"].append(f"  {n}: {lines} lines")

    # 11. Log rotation
    with open(LOG_PATH, 'r') as f:
        log_n = len(re.findall(r'^## \[', f.read(), re.MULTILINE))
    issues["INFO"].append(f"Log entries: {log_n}/500")

    # Output
    print(f"=== Wiki Lint ({TODAY.strftime('%Y-%m-%d')}) | {len(pages_data)} content pages ===")
    for sev in ["HIGH", "MEDIUM", "LOW", "INFO"]:
        items = issues[sev]
        if items:
            print(f"\n[{sev}]")
            for item in items:
                print(f"  {item}")
        else:
            print(f"\n[{sev}] OK")


if __name__ == "__main__":
    main()
