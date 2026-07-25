"""Regression tests for B101 assert removal fix in test_k8s_backend.py.

These tests verify that the if/raise AssertionError pattern used in
test_defaults_are_valid() correctly handles test failures (raises
AssertionError when conditions are not met) and is NOT an assert statement
(which would be caught by B101 and removed under -O).

The fix replaces:
    assert t.namespace == "espresense", "message"
With:
    if t.namespace != "espresense":
        raise AssertionError("message")

This matters because B101 (assert_used) flags assert statements as security
issues since they are removed under Python's -O/-OO optimization.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Resolve the repo root from this file's own location rather than hardcoding a
# path. The original version passed cwd="/work/repo" - the checkout path inside
# the container that generated this test - so the subprocess could only ever
# find the target test there, and the test failed with FileNotFoundError on
# every GitHub runner (and any local checkout).
REPO_ROOT = Path(__file__).resolve().parents[3]


class TestB101RegressionK8s:
    """Verify B101 fix in test_k8s_backend.py works correctly."""

    def test_defaults_valid_preserved_under_optimization(self):
        """Verify test_defaults_are_valid runs and passes with -O flag.

        This is the main regression test for the B101 fix. The test uses
        if/raise AssertionError instead of assert, which is NOT removed under
        -O (unlike assert statements).
        """
        result = subprocess.run(
            [
                sys.executable,
                "-O",  # Optimize: remove assert statements
                "-m",
                "pytest",
                "cli_anything/espresense/tests/test_k8s_backend.py::TestK8sTargetValidation::test_defaults_are_valid",
                "-v",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        # The if/raise pattern is NOT removed under -O, so it should pass.
        # If assert was still used, it would be removed and test would fail.
        if result.returncode != 0:
            import pytest
            pytest.fail(
                f"test_defaults_are_valid failed under -O optimization.\n"
                f"This indicates assertions were not properly replaced.\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

    def test_assert_would_be_removed_under_optimization(self):
        """Demonstrate that bare assert IS removed under -O (proving fix is needed).

        This proves B101 is a valid concern: assert statements are stripped,
        but the if/raise pattern is not (since it's not an assert statement).
        """
        import pytest
        # Test that assert False is removed under -O (no error)
        result_assert = subprocess.run(
            [sys.executable, "-O", "-c", "assert False, 'should be removed'"],
            capture_output=True,
            text=True,
        )
        # With -O, assert is stripped, so this succeeds (returncode 0)
        # Without -O, this would raise AssertionError (returncode 1)
        if result_assert.returncode != 0:
            pytest.fail(
                "assert should be removed under -O, but wasn't"
            )

    def test_if_raise_fires_on_wrong_default(self):
        """The if/raise pattern must raise AssertionError when a value is wrong.

        This directly verifies the behaviour change: the replaced checks are
        real runtime guards (not assert statements that vanish under -O).
        We patch the K8sTarget constructor so the returned object's namespace
        differs from what test_defaults_are_valid expects, and confirm the
        if/raise raises AssertionError (which a stripped assert would not).
        """
        import pytest
        from cli_anything.espresense.core import k8s_backend
        from cli_anything.espresense.tests.test_k8s_backend import (
            TestK8sTargetValidation,
        )

        tester = TestK8sTargetValidation()
        original = k8s_backend.K8sTarget

        class FakeTarget:
            namespace = "other-ns"
            deployment = "espresense-companion"
            container = "espresense-companion"
            config_path = "/config/espresense/config.yaml"

        k8s_backend.K8sTarget = FakeTarget
        try:
            with pytest.raises(AssertionError, match="namespace"):
                tester.test_defaults_are_valid()
        finally:
            k8s_backend.K8sTarget = original


class TestB101RegressionK8sAllFields:
    """Verify each if/raise guard in test_defaults_are_valid fires independently.

    The original B101 fix replaced 4 assert statements (lines 23-26) with
    if/raise AssertionError.  The existing TestB101RegressionK8s only covers
    the namespace field.  These tests cover the remaining 3 fields
    (deployment, container, config_path) to ensure every guard is a real
    runtime check that survives -O optimization.
    """

    def _run_with_fake_target(self, fake_target_cls, expected_match):
        """Helper: patch K8sTarget, run test_defaults_are_valid, expect AssertionError."""
        import pytest
        from cli_anything.espresense.core import k8s_backend
        from cli_anything.espresense.tests.test_k8s_backend import (
            TestK8sTargetValidation,
        )

        tester = TestK8sTargetValidation()
        original = k8s_backend.K8sTarget
        k8s_backend.K8sTarget = fake_target_cls
        try:
            with pytest.raises(AssertionError, match=expected_match):
                tester.test_defaults_are_valid()
        finally:
            k8s_backend.K8sTarget = original

    def test_if_raise_fires_on_wrong_deployment(self):
        """The deployment guard must raise AssertionError when wrong."""
        class FakeTarget:
            namespace = "espresense"
            deployment = "wrong-deployment"
            container = "espresense-companion"
            config_path = "/config/espresense/config.yaml"

        self._run_with_fake_target(FakeTarget, "deployment")

    def test_if_raise_fires_on_wrong_container(self):
        """The container guard must raise AssertionError when wrong."""
        class FakeTarget:
            namespace = "espresense"
            deployment = "espresense-companion"
            container = "wrong-container"
            config_path = "/config/espresense/config.yaml"

        self._run_with_fake_target(FakeTarget, "container")

    def test_if_raise_fires_on_wrong_config_path(self):
        """The config_path guard must raise AssertionError when wrong."""
        class FakeTarget:
            namespace = "espresense"
            deployment = "espresense-companion"
            container = "espresense-companion"
            config_path = "/wrong/path/config.yaml"

        self._run_with_fake_target(FakeTarget, "config_path")
