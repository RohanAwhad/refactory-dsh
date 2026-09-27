"""Discovery is useful to a restricted CEO, including negative coverage cases."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from refactory_dsh import console, discovery
from refactory_dsh.tools import gen_discovery


def test_docs_identity_search_sections_continuation_and_exact_paths():
    search = discovery.docs(query='worker routing', limit=1800)
    assert search['matches']
    assert len(json.dumps(search['matches'], ensure_ascii=False)) <= 1800
    page = 'refactory/docs/worker-routing.md'
    first = discovery.docs(page=page, limit=40)
    second = discovery.docs(page=page, offset=first['next_offset'], limit=40)
    assert first['text'] + second['text'] == discovery.docs(page=page, limit=80)['text']
    assert first['documentation_id'] == search['documentation_id']
    assert first['build_id'] == discovery.help_contract(Path('.'))['build_id']
    index = discovery.docs()['pages']
    for required in ('README.md', 'refactory/docs/concepts.md', 'refactory/docs/api/reference.md',
                     'refactory/docs/architecture.md', 'refactory/docs/optimization-profiles.md',
                     'refactory/docs/live-control.md'):
        assert required in {entry['page'] for entry in index}
    item = next(entry for entry in index if entry['page'] == page)
    assert discovery.docs(page=page, section=item['sections'][0]['id'])['text']
    assert all('.factory/' not in entry['page'] for entry in index)
    for invalid in ('../README.md', '/etc/passwd', 'refactory/docs/../../README.md', 'file:///etc/passwd'):
        with pytest.raises(ValueError, match='Unknown shipped'):
            discovery.docs(page=invalid)
    for limit in (0, -1, 12001):
        with pytest.raises(ValueError, match='limit'):
            discovery.docs(page=page, limit=limit)
    with pytest.raises(ValueError, match='section'):
        discovery.docs(page=page, section='missing-section')


def test_tampered_bundle_is_refused(tmp_path, monkeypatch):
    data = discovery.bundle()
    data['pages']['README.md']['text'] = 'stale or corrupted'
    (tmp_path / 'documentation.json').write_text(json.dumps(data))
    monkeypatch.setattr(discovery, 'DATA', tmp_path)
    with pytest.raises(ValueError, match='corrupt'):
        discovery.docs(page='README.md')


def test_build_is_deterministic_offline_and_detects_stale_artifact(tmp_path, monkeypatch, capsys):
    def no_process(*args, **kwargs):
        pytest.fail('documentation generation must not launch a process or model')
    monkeypatch.setattr(subprocess, 'Popen', no_process)
    first, errors = gen_discovery.generate()
    assert not errors
    assert first == gen_discovery.generate()[0]
    monkeypatch.setattr(gen_discovery, 'OUT', tmp_path / 'documentation.json')
    # Avoid the relative diagnostic assuming only repo outputs for this negative test.
    monkeypatch.setattr(gen_discovery, 'generate', lambda: ({Path('stale-help.json').absolute(): 'current'}, []))
    assert gen_discovery.main(['--check']) == 1
    assert 'CEO would see an older contract' in capsys.readouterr().err


def test_missing_operation_changed_argument_and_bad_mapping_have_actionable_diagnostics():
    authored = copy.deepcopy(discovery.coverage())
    ops = discovery.operations()
    ops['new-public-feature'] = {'description': 'New capability', 'parameters': {}}
    errors = gen_discovery.check_coverage(ops, [], {'operations': authored['operations'], 'modes': {}})
    assert any('new-public-feature' in error and 'CEO cannot choose/use' in error for error in errors)
    del ops['new-public-feature']
    ops['start']['parameters']['new-argument'] = {'type': 'string', 'default': None, 'required': False}
    errors = gen_discovery.check_coverage(ops, [], {'operations': authored['operations'], 'modes': {}})
    assert any('start: public schema changed' in error for error in errors)
    authored['operations']['start']['reviewed_schema'] = discovery.digest(discovery.operations_schema(ops['start']))
    assert gen_discovery.check_coverage(ops, [], {'operations': authored['operations'], 'modes': {}}) == []
    authored['operations']['docs']['chat']['fields']['unsafe'] = 'nonexistent'
    errors = gen_discovery.check_coverage(ops, [], {'operations': authored['operations'], 'modes': {}})
    assert any('chat argument unsafe' in error and 'CEO calls would fail' in error for error in errors)
    authored['operations']['docs']['examples'][0]['arguments']['invented'] = 'bad'
    assert any('example uses nonexistent argument' in error for error in gen_discovery.check_coverage(ops, [], {'operations': authored['operations'], 'modes': {}}))
    authored['operations']['docs']['examples'][0]['arguments']['limit'] = 'not-an-integer'
    errors = gen_discovery.check_coverage(ops, [], {'operations': authored['operations'], 'modes': {}})
    assert any('chat order must include every mapped field' in error for error in errors)
    assert any('example limit must be integer' in error for error in errors)


def test_new_runtime_mode_and_knob_visible_through_public_terminal(tmp_path):
    package = tmp_path / '.factory/packages/discovery_fixture'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('''from refactory_dsh import nn
from refactory_packages.counting_pack.mode import FixedStep
class Fixture(FixedStep):
    name = 'probe'
    @classmethod
    def declare(cls):
        return {**super().declare(), 'extra': nn.Knob(3, kind='int', lo=1, hi=8, description='Number of tiny examples')}
PACKAGE = nn.Package(name='discovery_fixture', description='A discovery fixture', modes=(Fixture,))
''')
    result = subprocess.run([sys.executable, '-m', 'refactory_dsh.console', 'help', '--project', str(tmp_path),
                             '--topic', 'discovery_fixture/probe'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    mode = json.loads(result.stdout)['mode']
    assert mode['parameters']['extra'] == {'value': 3, 'default': 3, 'kind': 'int', 'values': [],
                                          'range': [1, 8], 'frozen': False, 'description': 'Number of tiny examples'}
    assert mode['coverage_warnings']
    metadata = {'purpose': 'Exercise a tiny mode', 'doc': 'refactory/docs/discovery.md',
                'examples': [{'knobs': {'extra': 4}, 'explanation': 'Four examples.'}],
                'interfaces': {'cli': 'refactory ceo discovery_fixture/probe', 'chat': 'wf_open', 'panel': 'Mode picker'},
                'reviewed_schema': discovery.digest(discovery.mode_facts(mode))}
    assert gen_discovery.check_coverage({}, [mode], {'operations': {}, 'modes': {mode['id']: metadata}}) == []
    assert discovery.mode_help(mode, metadata)['coverage_warnings'] == []
    metadata['examples'][0]['knobs']['extra'] = 99
    assert any('invalid example knob extra' in error for error in gen_discovery.check_coverage({}, [mode], {'operations': {}, 'modes': {mode['id']: metadata}}))


def test_worker_configuration_and_unavailable_mutations_are_truthful(tmp_path):
    help = discovery.help_contract(tmp_path, 'workers')
    assert not help['chat']['fields']
    assert 'read-only' in help['purpose']
    assert help['configuration_schema']['backends']['codex']['provider_selection'] is False
    recovery = discovery.help_contract(tmp_path, 'graph-control')
    assert 'chat' not in recovery
    assert 'Unavailable' in recovery['interfaces']['chat']
    mode = discovery.help_contract(tmp_path, 'default_pack/design_fast')['mode']
    assert mode['action'] == 'run'
    assert mode['scored'] is False
    assert any(node['id'] == 'emit' and node['role'] for node in mode['nodes'])
    assert not mode['coverage_warnings']


def test_broken_document_links_have_a_build_diagnostic(tmp_path, monkeypatch):
    (tmp_path / 'README.md').write_text('# Guide\n\n[missing](refactory/docs/missing.md)\n')
    (tmp_path / 'refactory/docs').mkdir(parents=True)
    monkeypatch.setattr(gen_discovery, 'REPO_ROOT', tmp_path)
    _, errors = gen_discovery.read_pages('')
    assert any('README.md: broken documentation link' in error and 'CEO cannot follow' in error for error in errors)
