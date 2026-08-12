import re
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Match .strftime('format') where format includes a time component (%H, %I, or %p)
pattern = re.compile(r"\.strftime\('([^']*(?:%H|%I|%p)[^']*)'\)")

def replacer(m):
    fmt = m.group(1)
    return f" | ist('{fmt}')"

updated = []
for root, dirs, files in os.walk('templates'):
    dirs[:] = [d for d in dirs if d != '.git']
    for fname in files:
        if not fname.endswith('.html'):
            continue
        path = os.path.join(root, fname)
        content = open(path, encoding='utf-8').read()
        new_content = pattern.sub(replacer, content)
        if new_content != content:
            open(path, 'w', encoding='utf-8').write(new_content)
            updated.append(path)

for f in updated:
    print('Updated:', f)
print(f'Total files updated: {len(updated)}')
