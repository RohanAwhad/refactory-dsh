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
    # The target 'refactory_dsh.budgets.time.time' should resolve through the
    # owning module's imports.  When the owning module file is not on disk (as in
    # this unit test), the resolver falls back to checking whether the leaf of
    # the chain ('time.time') is allowlisted.  The test file imports ``time``,
    # so _resolve_through_imports maps the partial chain.  Either way the target
    # should NOT be flagged as a project-module violation.
    assert not violations, (
        f"expected no violations: 'time.time' is allowlisted, but got {violations}"
    )


def test_python_function_level_only_flags_patching_function():
    src = '''
def test_clean():
    assert 1 + 1 == 2

def test_patching(monkeypatch):
    monkeypatch.setattr('refactory_dsh.design_ops.check_artifact', lambda *a: None)
    assert True
'''
    clean = patch_scope.check_python_patches(
        src, "test_clean", ["refactory_dsh"], ["design_ops.py"],
    )
    assert not clean, "test_clean does not patch anything"

    patching = patch_scope.check_python_patches(
        src, "test_patching", ["refactory_dsh"], ["design_ops.py"],
    )
    assert patching, "test_patching should be flagged"


def test_typescript_vi_mock_flags_relative_import():
    src = '''
import { vi } from 'vitest';
import { helper } from '../src/helper';

vi.mock('../src/helper');

test('something', () => {
    expect(true).toBe(true);
});
'''
    violations = patch_scope.check_typescript_patches(
        src, "something", ["@project"], ["src/helper.ts"],
    )
    assert violations, "expected a violation for vi.mock of relative import"


def test_typescript_vi_mock_allows_external_package():
    src = '''
import { vi } from 'vitest';
vi.mock('@deepseek-ai/schemastery');

test('external', () => {
    expect(true).toBe(true);
});
'''
    violations = patch_scope.check_typescript_patches(
        src, "external", ["@project"], ["src/helper.ts"],
    )
    assert not violations, "external package should not be flagged"


def test_python_conftest_fixture_included():
    """A function that requests a conftest fixture that patches project code is flagged."""
    conftest_src = '''
import pytest

@pytest.fixture
def patched_check(monkeypatch):
    monkeypatch.setattr('refactory_dsh.design_ops.check_artifact', lambda *a: None)
'''
    test_src = '''
def test_uses_fixture(patched_check):
    assert True
'''
    # Parse the conftest to get its AST
    import ast as _ast
    conftest_tree = _ast.parse(conftest_src)
    # check_python_patches currently doesn't accept conftest_trees directly,
    # but the fixture detection looks in the test file's own AST plus conftest.
    # To test the conftest path, we combine them into one source.
    combined_src = conftest_src + "\n" + test_src
    violations = patch_scope.check_python_patches(
        combined_src, "test_uses_fixture", ["refactory_dsh"], ["design_ops.py"],
    )
    assert violations, (
        "expected a violation: conftest fixture patches project code on the path"
    )


def test_typescript_vi_spyon_flags_project_import():
    src = '''
import { vi } from 'vitest';
import { helper } from '../src/helper';

test('spy', () => {
    vi.spyOn(helper, 'parse');
    expect(true).toBe(true);
});
'''
    violations = patch_scope.check_typescript_patches(
        src, "spy", ["@project"], ["src/helper.ts"],
    )
    assert violations, "expected a violation for vi.spyOn on project import"
