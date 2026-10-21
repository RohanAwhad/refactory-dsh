"""Tests for patch_scope: AST-based patch-target detection."""
import pytest
from refactory_dsh import patch_scope


def test_python_monkeypatch_setattr_string_flags_project_module():
    src = '''
import pytest

def test_foo(monkeypatch):
    monkeypatch.setattr('refactory_dsh.design_ops.check_artifact', lambda *a: None)
    assert True
'''
    violations = patch_scope.check_python_patches(
        src, "test_foo", ["refactory_dsh"], ["design_ops.py"],
    )
    assert violations, "expected a violation for patching project module on path"


def test_python_patch_object_flags_project_module():
    src = '''
from unittest.mock import patch
from refactory_dsh import design_ops

def test_bar():
    with patch.object(design_ops, 'check_artifact'):
        pass
'''
    violations = patch_scope.check_python_patches(
        src, "test_bar", ["refactory_dsh"], ["design_ops.py"],
    )
    assert violations, "expected a violation for patch.object on project module"


def test_python_monkeypatch_setenv_allowed():
    src = '''
def test_env(monkeypatch):
    monkeypatch.setenv("HOME", "/tmp")
    assert True
'''
    violations = patch_scope.check_python_patches(
        src, "test_env", ["refactory_dsh"], ["design_ops.py"],
    )
    assert not violations, "setenv should be allowed"


def test_python_mock_patch_flags_project_module():
    src = '''
from unittest.mock import patch

@patch('refactory_dsh.base_check.compute_test_guardedness')
def test_guarded(mock_fn):
    assert True
'''
    violations = patch_scope.check_python_patches(
        src, "test_guarded", ["refactory_dsh"], ["base_check.py"],
    )
    assert violations, "expected a violation for @patch on project module"


def test_python_gh_stays_connected():
    src = '''
def test_gh(monkeypatch):
    monkeypatch.setattr('refactory_dsh.delivery._gh', lambda: None)
    assert True
'''
    violations = patch_scope.check_python_patches(
        src, "test_gh", ["refactory_dsh"], ["design_ops.py"],
    )
    assert not violations, "gh patch is not on the tested path (design_ops.py)"


def test_python_attribute_chain_resolves_to_allowlisted():
    src = '''
import time

def test_time(monkeypatch):
    monkeypatch.setattr('refactory_dsh.budgets.time.time', lambda: 0)
    assert True
'''
    violations = patch_scope.check_python_patches(
        src, "test_time", ["refactory_dsh"], ["budgets.py"],
    )
    # Should resolve to time.time (allowlisted) via the suffix fallback
    assert not violations, f"expected no violation (resolves to allowlisted): {violations}"


def test_python_function_level_only_flags_patching_function():
    src = '''
import pytest

def test_clean():
    assert True

def test_patching(monkeypatch):
    monkeypatch.setattr('refactory_dsh.design_ops.check_artifact', lambda *a: None)
    assert True
'''
    # Only test_patching should be flagged, not test_clean
    violations_clean = patch_scope.check_python_patches(
        src, "test_clean", ["refactory_dsh"], ["design_ops.py"],
    )
    violations_patching = patch_scope.check_python_patches(
        src, "test_patching", ["refactory_dsh"], ["design_ops.py"],
    )
    assert not violations_clean, "test_clean should not be flagged"
    assert violations_patching, "test_patching should be flagged"


def test_python_conftest_fixture_included():
    # Simulate a conftest fixture that patches project code
    src = '''
import pytest

def test_uses_fixture(patched_design_ops):
    assert True
'''
    # The fixture is defined in the same file for simplicity
    src_with_fixture = '''
import pytest

@pytest.fixture
def patched_design_ops(monkeypatch):
    monkeypatch.setattr('refactory_dsh.design_ops.check_artifact', lambda *a: None)

def test_uses_fixture(patched_design_ops):
    assert True
'''
    violations = patch_scope.check_python_patches(
        src_with_fixture, "test_uses_fixture", ["refactory_dsh"], ["design_ops.py"],
    )
    assert violations, "expected a violation via conftest fixture"


def test_typescript_vi_mock_flags_relative_import():
    src = '''
import { vi } from 'vitest';
vi.mock('../src/helper');

describe('test', () => {
    it('works', () => {
        expect(true).toBe(true);
    });
});
'''
    violations = patch_scope.check_typescript_patches(
        src, "works", [], ["src/helper.ts"],
    )
    assert violations, "expected a violation for vi.mock of relative import"


def test_typescript_vi_mock_allows_external_package():
    src = '''
import { vi } from 'vitest';
vi.mock('@deepseek-ai/schemastery');
'''
    violations = patch_scope.check_typescript_patches(
        src, "", [], ["src/helper.ts"],
    )
    assert not violations, "external package should not be flagged"


def test_typescript_vi_spyon_flags_project_import():
    src = '''
import { vi } from 'vitest';
import { helper } from '../src/helper';
vi.spyOn(helper, 'parse');
'''
    violations = patch_scope.check_typescript_patches(
        src, "", [], ["src/helper.ts"],
    )
    assert violations, "expected a violation for vi.spyOn on project import"
