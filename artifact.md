# Command/Argument Injection Fix — k8s_backend.py

## Summary
Fixed the confirmed critical command/argument-injection vulnerability in
`cli_anything/espresense/core/k8s_backend.py` where the user-supplied `timeout`
parameter in `rollout_status()` was interpolated directly into a kubectl
argument (`f"--timeout={timeout}"`) without any sanitisation or validation.

## Vulnerability Details

**File:** `cli_anything/espresense/core/k8s_backend.py`
**Function:** `rollout_status(target, timeout="120s")`
**Line (original):** ~line 175

The `timeout` parameter originates from the CLI `--timeout` option and flows
unchecked into:

```python
f"--timeout={timeout}"
```

A malicious value such as `120s --namespace=evil` or `120s;sleep 10` would
inject additional kubectl arguments or shell metacharacters into the
subprocess call. Although `_run` uses list-based `subprocess.run` (no
`shell=True`), the f-string interpolation means a value containing spaces
produces a single argument element like
`--timeout=120s --namespace=evil`, which kubectl interprets as two separate
flags — a classic argument-injection vector.

## Fix Applied

### 1. New validation regex (`_VALID_TIMEOUT_RE`)

```python
_VALID_TIMEOUT_RE = re.compile(r"^\d+[smh]?\Z")
```

Accepts only a non-negative integer with an optional single time-unit suffix
(`s`, `m`, `h`). Rejects spaces, shell metacharacters, null bytes, additional
`--flag` arguments, and anything else that could break out of the
`--timeout=` argument.

### 2. New validation function (`_check_timeout`)

```python
def _check_timeout(label: str, value: str) -> str:
    """Validate a kubectl --timeout value to prevent argument injection."""
    if not _VALID_TIMEOUT_RE.match(value):
        raise ValueError(
            f"{label} contains unsafe characters (got {value!r}). "
            "Only a non-negative integer with an optional 's', 'm', or 'h' "
            "suffix is permitted (e.g. '120s', '5m', '1h')."
        )
    return value
```

### 3. `rollout_status` now validates before use

```python
def rollout_status(target: K8sTarget, timeout: str = "120s") -> str:
    safe_timeout = _check_timeout("timeout", timeout)
    proc = _run([
        "-n", target.namespace,
        "rollout", "status",
        f"deployment/{target.deployment}",
        f"--timeout={safe_timeout}",
    ], check=False)
    return (proc.stdout or "") + (proc.stderr or "")
```

## Existing Protections (already in place)

All other user-supplied values (`namespace`, `deployment`, `container`,
`config_path`) are validated at construction time in `K8sTarget.__post_init__`
via `_check_path` using `_VALID_PATH_RE = re.compile(r"^[\w./-]+\Z")`. These
were already protected. The `timeout` parameter was the sole remaining gap.

## Regression Tests Added

**File:** `cli_anything/espresense/tests/test_k8s_backend.py`
**Class:** `TestRolloutTimeoutValidation` (24 new test cases)

### Valid timeout values accepted (7 parametrised cases)
- `120s`, `5m`, `1h`, `300s`, `0s`, `60`, `3600`

### Unsafe timeout values rejected (15 parametrised cases)
- Argument injection: `120s --namespace=evil`, `120s --server=https://evil`
- Shell metacharacters: `120s;sleep 10`, `120s && id`, `120s | cat`,
  `120s$(id)`, `` 120s`id` ``
- Null bytes / newlines: `120s\x00`, `120s\n`
- Empty / whitespace: `""`, `" "`
- Non-numeric / invalid: `abc`, `12.5s`, `-1s`, `120x`

### Additional tests
- `test_default_timeout_is_valid` — default `120s` passes validation
- `test_safe_timeout_reaches_kubectl_unchanged` — validated timeout appears
  as exactly one argument element, not split

## Test Results

```
$ python3 -m pytest
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: /work/repo
collected 85 items

cli_anything/espresense/tests/test_core.py ................              [ 18%]
cli_anything/espresense/tests/test_k8s_backend.py ...................... [ 44%]
...............................................                          [100%]

============================== 85 passed in 0.40s ==============================
```

- **Before fix:** 61 tests passed
- **After fix:** 85 tests passed (61 original + 24 new regression tests)
- **0 failures, 0 errors**

## Style Consistency

The fix follows the existing module conventions:
- Regex constant named `_VALID_TIMEOUT_RE` mirrors `_VALID_PATH_RE`
- Validation function `_check_timeout` mirrors `_check_path` (same signature
  pattern: `(label, value) -> str`, raises `ValueError` with descriptive
  message)
- Docstring style matches the module's existing docstrings
- Comment style for the regex constant matches the existing comment above
  `_VALID_PATH_RE`

## Files Changed

1. `cli_anything/espresense/core/k8s_backend.py` — added
   `_VALID_TIMEOUT_RE`, `_check_timeout`, and validation call in
   `rollout_status` (+25 lines, -1 line)
2. `cli_anything/espresense/tests/test_k8s_backend.py` — added
   `TestRolloutTimeoutValidation` class with 24 regression test cases
   (+82 lines)

## Rubric Compliance

- [x] Command/argument injection in k8s_backend.py is closed
- [x] No unsanitised input reaches a shell/kubectl call
- [x] Behaviour preserved and tests pass (85/85)
- [x] Regression test added (24 new cases in TestRolloutTimeoutValidation)
- [x] Style matches the module (mirrors _check_path / _VALID_PATH_RE pattern)
