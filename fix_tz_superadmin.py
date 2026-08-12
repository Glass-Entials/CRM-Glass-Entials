import re
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

pattern = re.compile(r"\.strftime\('([^']*(?:%H|%I|%p)[^']*)'\)")

def replacer(m):
    return f" | ist('{m.group(1)}')"

updated = []
for root, dirs, files in os.walk('super_admin'):
    dirs[:] = [d for d in dirs if d != '.git']
    for fname in files:
        if not fname.endswith('.html'):
            continue
        path = os.path.join(root, fname)
        content = open(path, encoding='utf-8').read()
        new = pattern.sub(replacer, content)
        if new != content:
            open(path, 'w', encoding='utf-8').write(new)
            updated.append(path)

for f in updated:
    print('Updated:', f)
print('Total:', len(updated))
