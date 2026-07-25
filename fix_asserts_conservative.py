import re

with open('cli_anything/espresense/tests/test_core.py', 'r') as f:
    lines = f.readlines()

final = []
for line in lines:
    # Only replace assert if it's the main statement on the line and not in a docstring
    # We check if the line matches the pattern and doesn't contain '"""'
    if re.match(r'^\s*assert\s+', line) and '"""' not in line:
        match = re.search(r'(\s*)assert\s+(.*?)(\s*#.*)?$', line)
        if match:
            indent = match.group(1)
            condition = match.group(2).strip().split(' #')[0].strip()
            final.append(f'{indent}if not {condition}: pytest.fail("Assertion failed")\n')
            continue
    final.append(line)

with open('cli_anything/espresense/tests/test_core.py', 'w') as f:
    f.writelines(final)
