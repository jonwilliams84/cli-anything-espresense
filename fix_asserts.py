import re
with open('cli_anything/espresense/tests/test_core.py', 'r') as f:
    lines = f.readlines()
final = []
for line in lines:
    if re.match(r'^\s*assert\s+', line):
        match = re.search(r'(\s*)assert\s+(.*?)(\s*#.*)?$', line)
        final.append(f'{match.group(1)}if not {match.group(2).strip().split(" #")[0].strip()}: pytest.fail("Assertion failed")\n')
    else:
        final.append(line)
with open('cli_anything/espresense/tests/test_core.py', 'w') as f:
    f.writelines(final)
