"""Upgrade regression tests against the installed, pinned harness and writer lock.

These run in the default Python suite after install.sh in CI. No model call or
live home is used; every profile and dependency fixture is under tmp_path.
"""
from __future__ import annotations

import json
import selectors
import shutil
import subprocess
from pathlib import Path

import pytest

from refactory_dsh import dsh_profile as profile
from refactory_dsh.paths import REPO_ROOT

BASE = "@deepseek-ai/dsh-base"
WEB_APP = "@deepseek-ai/dsh-web-app"
OLD = "@deepseek-ai/dsh-refactory-presets"
NEW = profile.PRESETS_BUNDLE
WEB = profile.WEB_SURFACE_BUNDLE
RETIRED = "refactory-web"


@pytest.fixture
def isolated_install(tmp_path, monkeypatch):
    if not shutil.which("node") or not shutil.which("pnpm") or not (REPO_ROOT / "node_modules/.bin/dsh").exists():
        pytest.skip("requires the pinned harness, Node and pnpm; run ./install.sh first")
    home = tmp_path / "dsh-home"
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("DSH_ROOT", str(REPO_ROOT))
    monkeypatch.delenv("REFACTORY_MODEL_DSH_HOME", raising=False)
    return home


def write_profile(home, name, bundles, dependencies=None):
    directory = home / "profiles" / name
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": f"dsh-profile-{name}", "private": True,
        "dependencies": dependencies or {},
        "dsh": {"profile": {"bundles": bundles, "patchReload": "live"}},
        "operatorMetadata": {"keep": ["original", 7]},
    }
    (directory / "package.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def local_dependency(bundle):
    return "link:" + str(REPO_ROOT / "packages/bundle" / profile.bundle_directory(bundle))


def seed_ready(home):
    write_profile(home, "graph", [BASE, profile.GRAPH_BUNDLE], {
        profile.GRAPH_BUNDLE: local_dependency(profile.GRAPH_BUNDLE),
    })
    write_profile(home, profile.GUI_PROFILE, [BASE, WEB_APP, NEW, WEB], {
        NEW: local_dependency(NEW), WEB: local_dependency(WEB),
    })


def read_profile(home, name):
    return json.loads((home / "profiles" / name / "package.json").read_text())


def run_launcher():
    result = subprocess.run(
        [str(REPO_ROOT / "refactory-dsh"), "--dump-config"], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
    return result


@pytest.mark.parametrize("dependencies,safe_order", [
    ([WEB], False), ([], False), ([OLD], False), ([OLD, WEB], True),
])
def test_retired_profile_matrix_converges_twice(isolated_install, tmp_path, dependencies, safe_order):
    home = isolated_install
    seed_ready(home)
    # An unrelated local dependency needs no registry access and must survive.
    other = tmp_path / "operator-dependency"
    other.mkdir()
    (other / "package.json").write_text(json.dumps({"name": "operator-dependency", "version": "1.0.0"}))
    deps = {name: local_dependency(name) for name in dependencies}
    deps["operator-dependency"] = "link:" + str(other)
    ordered = [OLD, WEB] if safe_order else [WEB, OLD]
    initial = write_profile(home, RETIRED, [BASE, WEB_APP, *ordered], deps)
    # Model the pre-upgrade link to the deleted bundle directory explicitly.
    node_modules = home / "profiles" / RETIRED / "node_modules" / "@deepseek-ai"
    node_modules.mkdir(parents=True)
    (node_modules / "dsh-refactory-presets").symlink_to(REPO_ROOT / "packages/bundle/refactory-presets")
    (node_modules / "dsh-refactory-web").symlink_to(REPO_ROOT / "packages/bundle/refactory-web")
    protected = {}
    for name in ("web", "martty", "operator"):
        write_profile(home, name, [BASE, OLD, WEB], {OLD: local_dependency(OLD)})
        path = home / "profiles" / name / "package.json"
        protected[path] = path.read_bytes()

    first = run_launcher()
    final = read_profile(home, RETIRED)
    assert final["dsh"]["profile"]["bundles"] == [BASE, WEB_APP]
    assert final["dependencies"] == {"operator-dependency": "link:" + str(other)}
    expected = json.loads(json.dumps(initial))
    expected["dependencies"] = final["dependencies"]
    expected["dsh"]["profile"]["bundles"] = [BASE, WEB_APP]
    assert final == expected
    backups = list((home / "profiles" / RETIRED).glob("package.json.refactory-backup-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == initial
    assert str(backups[0]) in first.stderr
    assert backups[0].stat().st_mode & 0o777 == 0o600
    before_second = (home / "profiles" / RETIRED / "package.json").read_bytes()
    run_launcher()
    assert (home / "profiles" / RETIRED / "package.json").read_bytes() == before_second
    assert profile.provision_plan() == []
    assert len(list(backups[0].parent.glob("package.json.refactory-backup-*"))) == 1
    assert all(path.read_bytes() == content for path, content in protected.items())


@pytest.mark.parametrize("stale_selection", [False, True])
def test_current_profile_repairs_selected_layer_and_cleans_retirement(isolated_install, stale_selection):
    home = isolated_install
    seed_ready(home)
    bundles = [BASE, WEB_APP, NEW, *([OLD] if stale_selection else []), WEB]
    initial = write_profile(home, profile.GUI_PROFILE, bundles, {
        WEB: local_dependency(WEB),
    })
    first = run_launcher()
    final = read_profile(home, profile.GUI_PROFILE)
    assert final["dsh"]["profile"]["bundles"] == [BASE, WEB_APP, NEW, WEB]
    assert final["dependencies"][NEW] == local_dependency(NEW)
    assert final["dependencies"][WEB] == local_dependency(WEB)
    assert OLD not in final["dependencies"]
    assert final["operatorMetadata"] == initial["operatorMetadata"]
    assert final["dsh"]["profile"]["patchReload"] == "live"
    assert ("backup:" in first.stderr) == stale_selection
    run_launcher()
    assert profile.provision_plan() == []
    assert read_profile(home, profile.GUI_PROFILE) == final


def test_retirement_resumes_after_cleanup_and_retains_its_backup(isolated_install):
    home = isolated_install
    seed_ready(home)
    initial = write_profile(home, RETIRED, [BASE, WEB_APP, OLD], {OLD: local_dependency(OLD)})
    report = profile.clean_manifest(RETIRED)
    assert OLD in read_profile(home, RETIRED)["dependencies"]
    assert [step.kind for step in profile.provision_plan()] == ["remove"]
    run_launcher()
    final = read_profile(home, RETIRED)
    assert final["dsh"]["profile"]["bundles"] == [BASE, WEB_APP]
    assert final.get("dependencies", {}) == {}
    assert json.loads(Path(report["backupPath"]).read_text()) == initial
    run_launcher()
    assert profile.provision_plan() == []
    assert read_profile(home, RETIRED) == final


def test_current_profile_rename_preserves_existing_layer_order(isolated_install):
    home = isolated_install
    seed_ready(home)
    write_profile(home, profile.GUI_PROFILE, [BASE, WEB_APP, OLD, WEB], {
        OLD: local_dependency(OLD), WEB: local_dependency(WEB),
    })
    run_launcher()
    final = read_profile(home, profile.GUI_PROFILE)
    # The replacement appends after retained layers; migration must not reorder
    # the operator's surviving selections to force one fixed permutation.
    assert final["dsh"]["profile"]["bundles"] == [BASE, WEB_APP, WEB, NEW]
    assert OLD not in final["dependencies"]
    assert NEW in final["dependencies"] and WEB in final["dependencies"]
    run_launcher()
    assert read_profile(home, profile.GUI_PROFILE) == final
    assert profile.provision_plan() == []


def test_fresh_home_is_provisioned_once(isolated_install):
    run_launcher()
    home = isolated_install
    final = read_profile(home, profile.GUI_PROFILE)
    assert final["dsh"]["profile"]["bundles"] == [BASE, WEB_APP, NEW, WEB]
    assert NEW in final["dependencies"] and WEB in final["dependencies"]
    assert profile.GRAPH_BUNDLE in read_profile(home, "graph")["dependencies"]
    run_launcher()
    assert read_profile(home, profile.GUI_PROFILE) == final
    assert profile.provision_plan() == []


def test_cleanup_reads_concurrent_changes_under_the_harness_lock(isolated_install):
    home = isolated_install
    seed_ready(home)
    write_profile(home, profile.GUI_PROFILE, [BASE, WEB_APP, NEW, OLD, WEB], {
        NEW: local_dependency(NEW), WEB: local_dependency(WEB),
    })
    manifest_path = home / "profiles" / profile.GUI_PROFILE / "package.json"
    # Hold the actual exported lock until stdin releases it. A READY handshake
    # establishes contention, rather than hoping scheduling overlaps two writes.
    script = r'''
import { readFile } from 'node:fs/promises';
import { loadAtomicWrite } from './refactory/refactory_dsh/profile_manifest.mjs';
const atomic = await loadAtomicWrite(process.cwd());
await atomic.withFileLock(process.argv[1], async () => {
  process.stdout.write('READY\n');
  await new Promise(resolve => process.stdin.once('data', resolve));
  const manifest = JSON.parse(await readFile(process.argv[1], 'utf8'));
  manifest.concurrentField = { kept: true };
  await atomic.writeFileAtomic(process.argv[1], JSON.stringify(manifest) + '\n', { mode: 0o600 });
});
'''
    holder = subprocess.Popen(
        ["node", "--input-type=module", "-e", script, str(manifest_path)],
        cwd=REPO_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(holder.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "lock holder did not start"
            assert holder.stdout.readline() == "READY\n"
        assert manifest_path.with_suffix(".json.lock").read_text() == f"{holder.pid}\n"
        before = manifest_path.read_bytes()
        with pytest.raises(profile.ProfileError, match="timed out waiting for the writer lock"):
            profile.clean_manifest(profile.GUI_PROFILE)
        assert manifest_path.read_bytes() == before
        assert not list(manifest_path.parent.glob("package.json.refactory-backup-*"))
        # Exercise the pinned plugin operation itself with a bounded lock wait.
        # The CLI supplies a much longer wait intended for package installation.
        operation = r'''
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const root = createRequire(process.cwd() + '/package.json');
const harness = createRequire(root.resolve('@deepseek-ai/dsh/package.json'));
const { runPluginCommand } = await import(pathToFileURL(harness.resolve('@deepseek-ai/dsh-plugin-manager/operations')).href);
await runPluginCommand({ dir: process.argv[1] }, ['add', process.argv[2]], { lockWaitMs: 100, outputBytes: 1024 });
'''
        plugin = subprocess.run(
            ["node", "--input-type=module", "-e", operation, str(manifest_path.parent),
             str(REPO_ROOT / "packages/bundle/refactory")],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
        assert plugin.returncode != 0
        assert "writer lock" in plugin.stdout + plugin.stderr
        assert manifest_path.read_bytes() == before
        _, error = holder.communicate("release\n", timeout=10)
        assert holder.returncode == 0, error
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=10)

    concurrent_preimage = manifest_path.read_bytes()
    report = profile.clean_manifest(profile.GUI_PROFILE)
    assert report["status"] == "rewritten"
    assert Path(report["backupPath"]).read_bytes() == concurrent_preimage
    final = read_profile(home, profile.GUI_PROFILE)
    assert final["concurrentField"] == {"kept": True}
    assert final["dsh"]["profile"]["bundles"] == [BASE, WEB_APP, NEW, WEB]
    assert not manifest_path.with_suffix(".json.lock").exists()
    run_launcher()
    assert read_profile(home, profile.GUI_PROFILE)["concurrentField"] == {"kept": True}
