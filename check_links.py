#!/usr/bin/env python3
"""Dead link analysis"""
import os

# Check: does mission-driven-organization exist anywhere?
found = False
for root, dirs, files in os.walk('wiki'):
    for f in files:
        if 'mission-driven' in f:
            found = True
            print(f'Found file: {os.path.join(root, f)}')
if not found:
    print('mission-driven-organization: NOT FOUND (true dead link)')

print()
print('=== Dead link analysis ===')

# Check each dead link
files_to_check = [
    ('wiki/concepts/digital-transformation-banking.md', 'five-key-articles'),
    ('wiki/concepts/inspire-positive-change.md', 'mission-driven-organization'),
    ('wiki/entities/weifang-bank.md', 'weifang-bank'),
    ('wiki/graph-report.md', 'wikilinks'),
]

for fpath, base in files_to_check:
    found_file = False
    for root, dirs, files in os.walk('wiki'):
        for f in files:
            if f == f'{base}.md':
                found_file = True
                full = os.path.join(root, f)
                print(f'  [[{base}...]] in {fpath}')
                print(f'    -> Base page exists: {full}')
                break
        if found_file:
            break
    if not found_file:
        print(f'  [[{base}]] in {fpath}')
        print(f'    -> DEAD LINK: page does not exist')

# Also check anchors
print()
print('=== Anchor checks ===')
anchor_checks = [
    ('wiki/concepts/five-key-articles.md', 'digital'),
    ('wiki/entities/weifang-bank.md', 'wei-ying'),
]
for fpath, anchor_keyword in anchor_checks:
    if os.path.exists(fpath):
        with open(fpath, 'r') as fh:
            content = fh.read()
        # Look for headings containing the keyword
        headings = [l for l in content.splitlines() if l.startswith('#')]
        matching = [h for h in headings if anchor_keyword in h.lower()]
        print(f'  {fpath}: looking for heading with "{anchor_keyword}"')
        if matching:
            for m in matching:
                print(f'    Found: {m}')
        else:
            print(f'    No matching heading found')
            print(f'    All headings: {headings[:10]}')
