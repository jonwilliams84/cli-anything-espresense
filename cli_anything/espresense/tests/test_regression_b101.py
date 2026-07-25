import pytest
from cli_anything.espresense.tests.test_k8s_backend import TestK8sTargetValidation

def test_defaults_are_still_valid():
    """Ensure the B101 fix didn't break the default validation test."""
    tester = TestK8sTargetValidation()
    tester.test_defaults_are_valid()
