import re

with open('cli_anything/espresense/tests/test_core.py', 'r') as f:
    content = f.read()

lines = content.splitlines()
final_lines = []
in_docstring = False

for line in lines:
    # Handle docstring boundaries
    if '"""' in line:
        # Simple toggle for docstrings
        # This is crude but for this file it might work if docstrings are on their own lines
        if line.count('"""') % 2 != 0:
            in_docstring = not in_docstring
    
    # If we are in a docstring, just fix f-strings and move on
    if in_docstring:
        if 'f"' in line:
            def replace_inner_quotes(m):
                return m.group(0).replace('"', "'")
            line = re.sub(r'\{[^\}]*\}', replace_inner_quotes, line)
        final_lines.append(line)
        continue

    # Fix f-strings for non-docstring lines
    if 'f"' in line:
        def replace_inner_quotes(m):
            return m.group(0).replace('"', "'")
        line = re.sub(r'\{[^\}]*\}', replace_inner_quotes, line)

    # Replace assert statements (only if not in docstring)
    if 'assert ' in line and not line.strip().startswith('#'):
        match = re.search(r'(\s*)assert\s+(.*?)(\s*#.*)?$', line)
        if match:
            indent = match.group(1)
            condition = match.group(2).strip().split(' #')[0].strip()
            final_lines.append(f'{indent}if not {condition}: pytest.fail("Assertion failed")')
            continue
    
    # Fix botched replacements
    if 'if not ' in line and 'pytest.fail(f"Assertion failed:' in line:
        match = re.search(r'(\s*)if not\s+(.*?):\s*pytest\.fail\(.*', line)
        if match:
            indent = match.group(1)
            condition = match.group(2).strip()
            final_lines.append(f'{indent}if not {condition}: pytest.fail("Assertion failed")')
            continue

    final_lines.append(line)

with open('cli_anything/espresense/tests/test_core.py', 'w') as f:
    f.write('\n'.join(final_lines) + '\n')
