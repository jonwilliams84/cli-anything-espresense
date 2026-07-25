import re

with open('cli_anything/espresense/tests/test_core.py', 'r') as f:
    content = f.read()

# Fix f-string syntax errors by replacing " with ' inside {}
def fix_fstring_brackets(text):
    def replace_inner(match):
        return match.group(0).replace('"', "'")
    return re.sub(r'\{[^\}]*\}', replace_inner, text)

# We only apply this to f-strings.
def replace_fstrings(match):
    return fix_fstring_brackets(match.group(0))

content = re.sub(r'f"[^"]*"', replace_fstrings, content)

# Replace all asserts with if not ...: pytest.fail
lines = content.splitlines()
final_lines = []
for line in lines:
    if 'assert ' in line and not line.strip().startswith('#'):
        match = re.search(r'(\s*)assert\s+(.*?)(\s*#.*)?$', line)
        if match:
            indent = match.group(1)
            condition = match.group(2).strip().split(' #')[0].strip()
            final_lines.append(f'{indent}if not {condition}: pytest.fail("Assertion failed")')
            continue
    final_lines.append(line)

with open('cli_anything/espresense/tests/test_core.py', 'w') as f:
    f.write('\n'.join(final_lines) + '\n')
