# B101 Security Fix - Regression Test Documentation

## Summary
Fixed 3 B101 (assert_used) security warnings in `cli_anything/espresense/tests/test_core.py` by replacing `assert` statements with `pytest.fail()` to prevent removal during Python `-O` optimization.

## Original Findings (Top 3)
| Line | Test Method | Original Code |
|------|-------------|---------------|
| 103 | test_rename_kitchen | `assert kitchen_node["room"] == "Cook Room"` |
| 110 | test_rename_strips_node_whitespace_globally | `assert noah["room"] == "Sophie Bedroom NEW"` |
| 114 | test_rename_strips_node_whitespace_globally | `assert bedroom["room"] == "Master Bedroom"` |
| 115 | test_rename_strips_node_whitespace_globally | `assert summary["whitespace_fixes"] >= 2` |

## Fix Applied
Replaced bare `assert` statements with explicit `pytest.fail()` conditional checks:

```python
# Line 103 (was):
assert kitchen_node["room"] == "Cook Room"
# Now:
if kitchen_node["room"] != "Cook Room":
    pytest.fail(f"Expected kitchen node room == 'Cook Room', got {kitchen_node['room']}")

# Line 110 (was):
assert noah["room"] == "Sophie Bedroom NEW"
# Now:
if noah["room"] != "Sophie Bedroom NEW":
    pytest.fail(f"Expected noah node room == 'Sophie Bedroom NEW', got {noah['room']}")

# Line 114 (was):
assert bedroom["room"] == "Master Bedroom"
# Now:
if bedroom["room"] != "Master Bedroom":
    pytest.fail(f"Expected bedroom node room == 'Master Bedroom', got {bedroom['room']}")

# Line 115 (was):
assert summary["whitespace_fixes"] >= 2
# Now:
if summary["whitespace_fixes"] < 2:
    pytest.fail(f"Expected whitespace_fixes >= 2, got {summary['whitespace_fixes']}")
```

## Regression Tests Added
Added 3 regression tests in `cli_anything/espresense/tests/test_security_fixes.py`:

1. **test_regression_rename_updates_kitchen_node_room_ref** (`TestB101Regression`)
   - Verifies kitchen node's room reference is updated after rename
   - Formerly line 103 assertion

2. **test_regression_rename_strips_whitespace_and_reassigns_node** (`TestB101Regression`)
   - Verifies whitespace is stripped and node reassigned to new room name
   - Formerly line 110 assertion

3. **test_regression_rename_strips_whitespace_without_renaming** (`TestB101Regression`)
   - Verifies whitespace-only fixes are counted correctly
   - Formerly lines 114+115 assertion

## Verification
- ✅ All 103 tests in espresense package pass
- ✅ Lines 103, 110, 114 in test_core.py no longer trigger B101
- ✅ Behavior preserved: same pass/fail conditions as original

## Git Commit
```
5f7ad51 Fix B101: Replace bare assert with pytest.fail() in test_core.py
```
