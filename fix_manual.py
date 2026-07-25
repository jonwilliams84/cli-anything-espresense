import re

with open('cli_anything/espresense/tests/test_core.py', 'r') as f:
    content = f.read()

# 1. Fix f-strings with nested double quotes
# Instead of complex regex, just replace the common ones
content = content.replace('n_noah["room"]', "n_noah['room']")
content = content.replace('n_sophie["room"]', "n_sophie['room']")
content = content.replace('n_spare["room"]', "n_spare['room']")
content = content.replace('n_master["room"]', "n_master['room']")

# 2. Replace all 'assert ' that are NOT in docstrings
# We'll split by docstring and only apply to non-docstring parts.
parts = re.split(r'( tripled_quote )', content) # This is not a real regex, just a placeholder
# Let's use a simpler way.

lines = content.splitlines()
final_lines = []
in_docstring = False
for line in lines:
    # Track docstring
    if '"""' in line:
        # This is a simple toggle. If there are two '"""' on one line, it doesn't toggle.
        if line.count('"""') % 2 != 0:
            in_docstring = not in_docstring
    
    if not in_docstring:
        # Replace assert
        if 'assert ' in line and not line.strip().startswith('#'):
            match = re.search(r'(\s*)assert\s+(.*?)(\s*#.*)?$', line)
            if match:
                indent = match.group(1)
                condition = match.group(2).strip().split(' #')[0].strip()
                line = f'{indent}if not {condition}: pytest.fail("Assertion failed")'
    
    final_lines.append(line)

with open('cli_anything/espresense/tests/test_core.py', 'w') as f:
    f.write('\n'.join(final_lines) + '\n')
