# B101 Security Fix - Regression Test Documentation

## Summary
Fixed 3 B101 (assert_used) security warnings in `cli_anything/espresense/tests/test_core.py` by replacing `assert` statements with `pytest.fail()` to prevent removal during Python `-O` optimization.

## Original Findings
| Line | Original Code | Issue |
|------|--------------|-------|
| 94 | `assert summary["rooms_renamed"] == 1` | B101: assert used |
| 95 | `assert summary["nodes_repointed"] == 1` | B101: assert used |
| 96 | `assert parsed["floors"][0]["rooms"][0]["name"] == "Cook Room"` | B101: assert used |

## Fix Applied
Replaced bare `assert` statements with `pytest.fail()` conditional checks:

```python
if summary["rooms_renamed"] != 1:
    pytest.fail(f"Expected rooms_renamed == 1, got {summary['rooms_renamed']}")
if summary["nodes_repointed"] != 1:
    pytest.fail(f"Expected nodes_repointed == 1, got {summary['nodes_repointed']}")
if parsed["floors"][0]["rooms"][0]["name"] != "Cook Room":
    pytest.fail(f"Expected room name 'Cook Room', got {parsed['floors'][0]['rooms'][0]['name']}")
```

## Regression Tests Added
Added 3 regression tests in `TestB101Regression` class:

1. **test_rename_returns_rooms_renamed_count**
   - Verifies rename() returns correct rooms_renamed count
   - Formerly line 94 assertion

2. **test_rename_returns_nodes_repointed_count**
   - Verifies rename() returns correct nodes_repointed count
   - Formerly line 95 assertion

3. **test_rename_updates_floor_room_name**
   - Verifies floor data structure is updated after rename
   - Formerly line 96 assertion

## Verification
- All 22 tests in test_core.py pass (was 19, now 22)
- All 100 tests in espresense package pass
- Bandit no longer reports B101 on lines 94, 95, or 96
- Behavior preserved: same pass/fail conditions as original

## Git Commit
```
7ed1d15 Fix B101: Replace assert with pytest.fail in test_simple_rename
```
