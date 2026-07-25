import re

with open('cli_anything/espresense/tests/test_core.py', 'r') as f:
    lines = f.readlines()

final_lines = []
for line in lines:
    # Remove lines that look like botched replacements of docstrings
    # These usually have a weird structure like ' if not ... (line 78).""": pytest.fail...'
    if ' (line ' in line and 'pytest.fail' in line:
        continue
    final_lines.append(line)

# Now we have a cleaner file. Let's fix the f-strings and asserts again.
content = "".join(final_lines)

# Fix f-string nested quotes (simple approach)
content = content.replace('n_noah["room"]', "n_noah['room']")
content = content.replace('n_sophie["room"]', "n_sophie['room']")
content = content.replace('n_spare["room"]', "n_spare['room']")
content = content.replace('n_master["room"]', "n_master['room']")

# Replace all asserts with if not...
lines = content.splitlines()
final_lines = []
for line in lines:
    if re.match(r'^\s*assert\s+', line):
        match = re.search(r'(\s*)assert\s+(.*?)(\s*#.*)?$', line)
        indent = match.group(1)
        condition = match.group(2).strip().split(' #')[0].strip()
        final_lines.append(f'{indent}if not {condition}: pytest.fail("Assertion failed")')
    else:
        final_lines.append(line)

with open('cli_anything/espresense/tests/test_core.py', 'w') as f:
    f.write('\n'.join(final_lines) + '\n')
