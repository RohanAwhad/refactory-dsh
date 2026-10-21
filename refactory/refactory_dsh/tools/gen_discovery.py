"""Offline coverage gate and deterministic docs/tool contract generator.

Semantic approvals in data/coverage.json are deliberately never auto-updated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from refactory_dsh import discovery
from refactory_dsh.paths import REPO_ROOT

OUT = discovery.DATA / 'documentation.json'
TS_OUT = REPO_ROOT / 'packages/refactory/tool-refactory/src/generated-help.ts'
REF_OUT = REPO_ROOT / 'refactory/docs/api/capabilities.md'


def sections(text):
    found = [{'title': match.group(2).strip(), 'start': match.start()}
             for match in re.finditer(r'^(#{1,6})\s+(.+)$', text, re.M)]
    if not found or found[0]['start']:
        found.insert(0, {'title': 'Introduction', 'start': 0})
    counts = {}
    for i, item in enumerate(found):
        slug = re.sub(r'[^\w\s-]', '', item['title'].lower()).replace(' ', '-')
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        item.update(id=slug + (f'-{count}' if count else ''), end=found[i+1]['start'] if i+1 < len(found) else len(text))
    return found


def read_pages(reference):
    pages = {}
    paths = [REPO_ROOT / 'README.md', *sorted((REPO_ROOT / 'refactory/docs').rglob('*.md'))]
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(REPO_ROOT.resolve()):
            raise ValueError(f'Product documentation must be an installation-owned file: {path}')
        text = reference if path == REF_OUT else path.read_text()
        page_id = path.relative_to(REPO_ROOT).as_posix()
        headings = sections(text)
        pages[page_id] = {'title': headings[0]['title'], 'text': text, 'sections': headings,
                          'sha256': hashlib.sha256(text.encode()).hexdigest(), 'links': []}
    errors = []
    for page_id, entry in pages.items():
        for target in re.findall(r'\]\(([^)]+)\)', entry['text']):
            target = target.split(' "', 1)[0].strip('<>')
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                entry['links'].append({'target': target, 'external': True})
                continue
            normalized = posixpath.normpath(posixpath.join(posixpath.dirname(page_id), unquote(parsed.path))) if parsed.path else page_id
            if normalized.startswith('../') or not (REPO_ROOT / normalized).exists():
                errors.append(f'{page_id}: broken documentation link {target}; CEO cannot follow this reference')
            if normalized in pages and parsed.fragment:
                fragment = unquote(parsed.fragment)
                anchors = {section['id'] for section in pages[normalized]['sections']}
                anchors.update(re.findall(r'<a\s+id=["\']([^"\']+)', pages[normalized]['text']))
                if fragment not in anchors:
                    errors.append(f'{page_id}: missing section {target}; fix the link or heading')
            entry['links'].append({'target': target, 'page': normalized if normalized in pages else None,
                                    'section': unquote(parsed.fragment),
                                    **({} if normalized in pages else {'route': 'repository asset; not a docs page'})})
    return dict(sorted(pages.items())), errors


def check_coverage(ops, modes, authored):
    errors = []
    from refactory_dsh.nn.knob import Knob
    for group, facts in (('operations', ops), ('modes', {mode['id']: mode for mode in modes})):
        for key, entry in facts.items():
            meta = authored.get(group, {}).get(key, {})
            fingerprint = discovery.digest(discovery.operations_schema(entry) if group == 'operations' else discovery.mode_facts(entry))
            fix = f'Edit refactory/refactory_dsh/data/coverage.json {group}.{key}, review guidance/examples/interfaces, then set reviewed_schema={fingerprint}'
            if not meta.get('purpose') or not meta.get('examples') or not meta.get('doc'):
                errors.append(f'{key}: missing purpose, worked examples or documentation; CEO cannot choose/use this capability reliably. {fix}')
            if meta.get('reviewed_schema') != fingerprint:
                errors.append(f'{key}: public schema changed or is uncovered; CEO guidance may omit new arguments/defaults. {fix}')
            if not meta.get('interfaces') or any(not meta['interfaces'].get(route) for route in ('cli', 'chat', 'panel')):
                errors.append(f'{key}: declare CLI, chat and panel routes or an explicit reason each is unavailable. {fix}')
            doc = meta.get('doc', '')
            if not doc or not (REPO_ROOT / doc).is_file():
                errors.append(f'{key}: documentation page {doc!r} is missing. {fix}')
            if group == 'operations':
                chat = meta.get('chat')
                if chat:
                    order = chat.get('order', list(chat['fields']))
                    if len(order) != len(set(order)) or set(order) != set(chat['fields']):
                        errors.append(f'{key}: chat order must include every mapped field exactly once; advertised arguments would be ignored. {fix}')
                    for alias, dest in chat['fields'].items():
                        if dest not in entry['parameters']:
                            errors.append(f'{key}: chat argument {alias} maps to nonexistent {dest}; CEO calls would fail. {fix}')
                    supported = set(chat['fields'].values())
                    for param, spec in entry['parameters'].items():
                        if spec['required'] and param not in supported:
                            errors.append(f'{key}: required {param} is unavailable through advertised chat action. {fix}')
                    for required in chat.get('required', []):
                        if required not in chat['fields']:
                            errors.append(f'{key}: unknown required chat field {required}. {fix}')
                for example in meta.get('examples', []):
                    if not isinstance(example, dict) or not isinstance(example.get('arguments'), dict) or not example.get('explanation'):
                        errors.append(f'{key}: example must contain arguments and explanation. {fix}')
                        continue
                    missing = [param for param, spec in entry['parameters'].items()
                               if spec['required'] and param not in example['arguments']]
                    if missing:
                        errors.append(f'{key}: example omits required arguments {missing}. {fix}')
                    for param, value in example['arguments'].items():
                        spec = entry['parameters'].get(param)
                        if spec is None:
                            errors.append(f'{key}: example uses nonexistent argument {param}. {fix}')
                        elif spec.get('enum') and value not in spec['enum']:
                            errors.append(f'{key}: example uses unsupported {param}={value}. {fix}')
                        elif not isinstance(value, {'string': str, 'integer': int, 'number': (int, float),
                                                   'boolean': bool, 'object': dict, 'array': list}[spec['type']]):
                            errors.append(f'{key}: example {param} must be {spec["type"]}. {fix}')
            else:
                for example in meta.get('examples', []):
                    for name, value in example.get('knobs', {}).items():
                        param = entry.get('parameters', {}).get(name)
                        if param is None:
                            errors.append(f'{key}: example uses unknown knob {name}. {fix}')
                        else:
                            try:
                                Knob(default=param['default'], kind=param['kind'], lo=param['range'][0], hi=param['range'][1],
                                     enum=tuple(param['values']) if param['kind'] == 'enum' else (),
                                     values=tuple(param['values']) if param['kind'] == 'str' else ()).coerce(value)
                            except ValueError as error:
                                errors.append(f'{key}: invalid example knob {name}: {error}. {fix}')
        for key in set(authored.get(group, {})) - set(facts):
            errors.append(f'{key}: coverage describes a removed public capability; remove or migrate its guidance')
    return errors


def tool_contract(ops, authored):
    chats, parameters = {}, {}
    for name, entry in ops.items():
        meta = authored['operations'].get(name, {})
        chat = meta.get('chat')
        if not chat:
            continue
        action = chat.get('action', name)
        chats[action] = {**chat, 'verb': name, 'purpose': meta['purpose'], 'order': chat.get('order', list(chat['fields'])), 'arguments': {}}
        for alias, dest in chat['fields'].items():
            spec = entry['parameters'][dest]
            chats[action]['arguments'][alias] = spec
            parameter = {key: spec[key] for key in ('type', 'items', 'enum') if key in spec}
            if parameter.get('type') == 'object':
                parameter.setdefault('additionalProperties', True)
            parameter['description'] = f"{action}: {spec['description'] or meta['purpose']} (console {dest}). Read help topic={name} for defaults and constraints."
            if alias in parameters:
                previous = parameters[alias]
                if previous['type'] != parameter['type']:
                    raise ValueError(f'Incompatible chat parameter {alias}; use distinct names for {action}')
                # Different enum domains cannot be represented by the intersection.
                if 'enum' in previous or 'enum' in parameter:
                    if 'enum' in previous and 'enum' in parameter:
                        parameter['enum'] = sorted(set(previous['enum']) | set(parameter['enum']))
                    else:
                        parameter.pop('enum', None)
                parameter['description'] = previous['description'] + ' ' + parameter['description']
            parameters[alias] = parameter
    parameters['headless'] = {'type': 'boolean', 'description': 'Required true with approve: caller explicitly says nobody is at the keyboard; otherwise use wf_open so the runtime asks user gates.'}
    parameters['settings'] = {'type': 'object', 'additionalProperties': True, 'description': 'Explicit start/profile-resolve settings from help topic=start. Uses actual console flags; JSON route maps are supported. Profile and project defaults apply to omitted fields.'}
    parameters['action'] = {'type': 'string', 'required': True, 'enum': list(chats), 'description': 'Read help first; docs searches/reads only shipped documentation. ' + ' '.join(f'{name}: {value["purpose"]}' for name, value in chats.items())}
    return chats, parameters


def render_reference(ops, modes, authored):
    lines = ['<!-- Generated by refactory_dsh.tools.gen_discovery; do not edit. -->', '# Live capability reference', '',
             'Use `refactory help --topic <ID>` for current runtime schemas and `refactory docs` for the shipped documentation index.', '']
    for key, value in ops.items():
        meta = authored['operations'].get(key, {})
        lines += [f'## {key}', '', meta.get('purpose', 'MISSING GUIDANCE'), '',
                  f"Documentation: [{meta.get('doc', '')}](../../../{meta.get('doc', '')})", '',
                  '```json', json.dumps({**value, **meta}, indent=2, sort_keys=True), '```', '']
    for value in modes:
        meta = authored['modes'].get(value['id'], {})
        lines += [f"## {value['id']}", '', meta.get('purpose', 'MISSING GUIDANCE'), '', '```json',
                  json.dumps({**discovery.mode_facts(value), 'usage': meta}, indent=2, sort_keys=True), '```', '']
    return '\n'.join(lines)


def generate():
    from refactory_dsh.console import catalogue
    authored = discovery.coverage()
    ops = discovery.operations()
    # No user project, installed extras or saved package preferences enter a build.
    with tempfile.TemporaryDirectory(prefix='refactory-discovery-') as empty:
        modes = catalogue(project=Path(empty))
    errors = check_coverage(ops, modes, authored)
    if errors:
        return {}, errors
    reference = render_reference(ops, modes, authored)
    pages, errors = read_pages(reference)
    if errors:
        return {}, errors
    chats, parameters = tool_contract(ops, authored)
    guide = (REPO_ROOT / 'refactory/docs/ceo-guide.md').read_text()
    version = json.loads((REPO_ROOT / 'package.json').read_text())['version']
    documentation_id = discovery.digest(pages)
    build_id = discovery.digest({'operations': ops, 'modes': [discovery.mode_facts(m) for m in modes],
                                 'coverage': authored, 'documentation_id': documentation_id, 'runtime_version': version})
    document = {'schema_version': discovery.SCHEMA_VERSION, 'runtime_version': version, 'build_id': build_id,
                'documentation_id': documentation_id, 'operating_guide': guide, 'pages': pages}
    ts = '// Generated offline by refactory_dsh.tools.gen_discovery. Do not edit.\n'
    for name, value in (('CHAT', chats), ('PARAMETERS', parameters), ('OPERATING_GUIDE', guide), ('BUILD_ID', build_id)):
        ts += f'export const {name} = {json.dumps(value, indent=2, sort_keys=True)} as const\n'
    return {OUT: json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + '\n', TS_OUT: ts, REF_OUT: reference}, []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    artifacts, errors = generate()
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    for path, text in artifacts.items():
        if args.check:
            if not path.exists() or path.read_text() != text:
                errors.append(f'{path.relative_to(REPO_ROOT)}: stale generated help/docs; CEO would see an older contract. Run pnpm run docs:generate, review the diff, then pnpm run docs:check.')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('discovery: coverage and generated installation documentation are current')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
