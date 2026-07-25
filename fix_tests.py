import re

with open('cli_anything/espresense/tests/test_core.py', 'r') as f:
    content = f.read()

lines = content.splitlines()
final_lines = []

for line in lines:
    # 1. Fix existing f-string syntax errors (nested double quotes)
    if 'f"' in line:
        def replace_inner_quotes(m):
            return m.group(0).replace('"', "'")
        line = re.sub(r'\{[^\}]*\}', replace_inner_quotes, line)

    # 2. Replace assert statements
    if 'assert ' in line and not line.strip().startswith('#'):
        match = re.search(r'(\s*)assert\s+(.*?)(\s*#.*)?$', line)
        if match:
            indent = match.group(1)
            condition = match.group(2).strip()
            condition = condition.split(' #')[0].strip()
            # To avoid all quote issues, we use % formatting or .format() with a carefully chosen quote
            # But the simplest is to just avoid f-strings for the failure message.
            msg = 'Assertion failed: ' + condition
            # If msg contains double quotes, we wrap it in single quotes, and vice versa.
            if '"' in msg and "'" not in msg:
                quote_wrap = "'" + msg + "'"
            elif "'" in msg and '"' not in msg:
                quote_wrap = '"' + msg + '"'
            else:
                # Both quotes present, we must escape or use a different approach.
                # Let's use repr() of the condition.
                quote_wrap = f"pytest.fail(f'Assertion failed: {{repr({condition})}}')"
                # Wait, this is getting complex. Let's just use a simple string:
                # pytest.fail("Assertion failed")
                quote_wrap = '"Assertion failed"'
            
            # Actually, let's just use:
            final_lines.append(f'{indent}if not {condition}: pytest.fail("Assertion failed")')
        else:
            final_lines.append(line)
    else:
        final_lines.append(line)

with open('cli_anything/espresense/tests/test_core.py', 'w') as f:
    f.write('\n'.join(final_lines) + '\n')
