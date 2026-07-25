import re

with open('cli_anything/espresense/tests/test_core.py', 'r') as f:
    content = f.read()

def fix_fstring(text):
    lines = text.splitlines()
    fixed_lines = []
    for line in lines:
        # If line has f" and inside {...} it has "
        if 'f"' in line:
            # Find everything between { and }
            def replace_inner(m):
                return m.group(0).replace('"', "'")
            line = re.sub(r'\{[^\}]*\}', replace_inner, line)
        fixed_lines.append(line)
    return '\n'.join(fixed_lines)

content = fix_fstring(content)
with open('cli_anything/espresense/tests/test_core.py', 'w') as f:
    f.write(content + '\n')
