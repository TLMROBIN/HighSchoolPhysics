"""Fixed, restartable paper ingestion. No LLM is allowed to choose the next step.

Run on the deployment host as the service owner; uses the same domain operations
as the HTTP UI. State/evidence live outside the application DB. A separate
watchdog process can set STOP even while a model request is in progress.
"""
from __future__ import annotations

import argparse
import base64
import copy
import fcntl
import hashlib
import io
import posixpath
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager

from . import document_ingestion as ingestion, question_bank
from .db import connect
from .document_models import canonical_sha256, validate_question_document, ASSET_URI_RE
from .repository import PhysicsRepository
from .errors import DomainError
from . import ingestion_model

VERSION = 1
STAGES = ('upload_paper', 'wait_paper', 'review_paper', 'confirm_paper',
          'upload_answers', 'wait_answers', 'attach_answers', 'review_answers',
          'watchdog_content', 'publish', 'generate_tags', 'watchdog_tags', 'adopt_tags', 'verify', 'complete')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
ROLE_RE = re.compile(r'[（(](原卷版|空白卷|解析版|解析卷)[）)]')


class Halt(RuntimeError):
    pass


def atomic_json(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pipeline-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            try:
                os.link(name, path)
            except FileExistsError:
                pass
        else:
            os.replace(name, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def source_numbers(path):
    """Independent raw OOXML preflight; never invent invisible auto-numbering."""
    if Path(path).suffix.lower() != '.docx':
        return []
    from .document_store import sniff_document
    sniff_document(path, Path(path).name)
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    numbers = []
    for paragraph in root.iter(W+'p'):
        text = ''.join(t.text or '' for t in paragraph.iter(W+'t'))
        match = re.match(r'^\s*(\d{1,3})[.．、](?!\d)', text)
        if match:
            numbers.append(match[1])
    if len(numbers) != len(set(numbers)):
        return []
    return numbers


def raw_question_xml(path, number):
    """Independent source text/math structure, not the parser's rewritten Markdown."""
    if Path(path).suffix.lower() != '.docx':
        return None
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    body = root.find(W+'body')
    selected = []
    active = False
    for node in body:
        text = ''.join(t.text or '' for t in node.iter(W+'t'))
        match = re.match(r'^\s*(\d{1,3})[.．、](?!\d)', text)
        if match:
            if active:
                break
            active = match[1] == number
        if active:
            # Keep original math, text, scripts and image relation identifiers;
            # drop Word styling/positioning noise, never truncate mathematical data.
            selected.append({'text': text,
                'math_xml': [ET.tostring(n, encoding='unicode') for n in node.iter('{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath')],
                'scripts': [ET.tostring(n, encoding='unicode') for n in node.iter(W+'vertAlign')],
                'embedded_objects': sum(1 for n in node.iter(W+'object'))})
    return selected or None


def inventory(roots):
    groups = {}
    ignored = []
    for root in roots:
        for path in sorted(Path(root).rglob('*')):
            if not path.is_file() or path.name.startswith('~$'):
                continue
            if path.suffix.lower() not in ('.docx', '.doc', '.pdf'):
                ignored.append(str(path)); continue
            match = ROLE_RE.search(path.stem)
            if not match:
                ignored.append(str(path)); continue
            key = str(path.parent.resolve())+'/'+ROLE_RE.sub('', path.stem)
            role = 'paper' if match[1] in ('原卷版', '空白卷') else 'answers'
            groups.setdefault(key, {}).setdefault(role, []).append(path.resolve())
    result = []
    for key, roles in sorted(groups.items()):
        entry = {'title': Path(key).name, 'files': {}, 'issues': []}
        for role in ('paper', 'answers'):
            choices = roles.get(role, [])
            if len(choices) != 1:
                entry['issues'].append(role+'_missing_or_ambiguous'); continue
            path = choices[0]
            entry['files'][role] = {'path': str(path), 'sha256': digest_file(path)}
        if 'paper' in entry['files']:
            entry['expected_numbers'] = source_numbers(entry['files']['paper']['path'])
            if not entry['expected_numbers']:
                entry['issues'].append('question_numbers_need_explicit_manifest')
        entry['partial'] = bool(re.search(r'部分|\d[-—–]\d|\d、\d', entry['title']))
        result.append(entry)
    return {'version': VERSION, 'papers': result, 'ignored': ignored}


def apply_patches(document, result, stage):
    """Validate model output before touching the DB. Structure and evidence are immutable."""
    if result.get('verdict') != 'pass' or not isinstance(result.get('reason'), str) or len(result['reason'].strip()) < 8:
        raise Halt('review_blocked: '+str(result.get('reason', 'invalid review verdict')))
    checks = result.get('checks')
    required = ('number', 'stem', 'options', 'formulas', 'children') if stage == 'paper' else ('answer',)
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in required):
        raise Halt('review_checks_incomplete')
    patches = result.get('patches', [])
    if not isinstance(patches, list) or len(patches) > 40:
        raise Halt('invalid_patch_list')
    out = copy.deepcopy(document)
    allowed = {'stem_md', 'options'} if stage == 'paper' else {'answer_md', 'analysis_md'}
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) != {'target', 'field', 'value'} or patch['field'] not in allowed:
            raise Halt('patch_outside_stage')
        target = out if patch['target'] == 'parent' else next((c for c in out['children'] if c['key'] == patch['target']), None)
        if target is None:
            raise Halt('unknown_patch_target')
        value = patch['value']
        if patch['field'] == 'options':
            if not isinstance(value, list) or [x.get('key') for x in value if isinstance(x, dict)] != [x['key'] for x in target.get('options', [])] or len(value) != len(target.get('options', [])):
                raise Halt('option_structure_changed')
        elif not isinstance(value, str) or len(value) > 100000:
            raise Halt('invalid_patch_text')
        target[patch['field']] = value
    # No new, moved or removed figure references via prose edits.
    def refs(value):
        return sorted(ASSET_URI_RE.findall(json.dumps(value, ensure_ascii=False)))
    if refs(out) != refs(document):
        raise Halt('figure_references_changed')
    if stage == 'answers':
        units = out['children'] or [out]
        if any(not u.get('answer_md', '').strip() for u in units):
            raise Halt('child_answer_missing')
        for unit in [out]+out['children']:
            if unit.get('answer_md', '').strip():
                unit['answer_state'] = 'verified'
    if stage == 'answers':
        from .choice_answers import prepare_answers
        out = prepare_answers(out)
        if any(u.get('answer_state') != 'verified' for u in (out['children'] or [out])):
            raise Halt('answer_key_not_unambiguous')
    return validate_question_document(out)


def sample_ids(items, seed, rate=0.2):
    """Always include ends, all risk/figure/multi-part items, and a frozen hash sample."""
    if not items:
        raise Halt('empty_paper')
    chosen = {items[0]['id'], items[-1]['id']}
    for item in items:
        d = item['document']
        if d.get('issues') or d.get('asset_refs') or d.get('children') or '$' in json.dumps(d, ensure_ascii=False):
            chosen.add(item['id'])
    ranked = sorted(items, key=lambda i: hashlib.sha256((seed+i['id']).encode()).hexdigest())
    chosen.update(i['id'] for i in ranked[:max(1, math.ceil(len(items)*rate))])
    return [i['id'] for i in items if i['id'] in chosen]


@contextmanager
def locked(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory/'runner.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Halt('runner_already_active') from exc
        yield


class Pipeline:
    def __init__(self, directory, reviewer=None):
        self.directory = Path(directory).resolve()
        self.path = self.directory/'state.json'
        self.state = json.loads(self.path.read_text())
        if self.state['version'] != VERSION:
            raise Halt('workflow_version_changed')
        if not Path(self.state['db']).is_file():
            raise Halt('database_missing')
        self.conn = connect(self.state['db'])
        self.repo = PhysicsRepository(self.conn)
        self.actor = self.repo._require_question_bank_actor(self.state['actor_id'])
        if self.actor['school_id'] != self.state['school_id']:
            raise Halt('actor_school_changed')
        self.reviewer = reviewer or ingestion_model.review
        self.store = ingestion._store_for_db(self.state['db'])

    def close(self):
        self.conn.close()

    def save(self):
        self.state['updated_at'] = time.time()
        atomic_json(self.path, self.state)

    def check_stop(self):
        for info in self.state['paper']['files'].values():
            if digest_file(info['path']) != info['sha256']:
                raise Halt('source_file_changed')
        if (self.directory/'STOP.json').exists():
            raise Halt('watchdog_stop: inspect STOP.json; explicit resume required')

    def halt(self, reason):
        atomic_json(self.directory/'STOP.json', {'reason': reason, 'at': time.time(), 'stage': self.state['stage']}, exclusive=True)

    def key(self, name):
        return 'pipeline-'+canonical_sha256([self.state['run_id'], name])[:48]

    def operation(self, name, payload, fn):
        """Persist exact request BEFORE mutation; replay the same request after a crash."""
        self.check_stop()
        actions = self.state.setdefault('actions', {})
        if name not in actions:
            actions[name] = {'payload': payload}
            self.save()
        action = actions[name]
        if 'result' not in action:
            self.check_stop()
            action['result'] = fn(action['payload'])
            self.save()
        return action['result']

    def call_model(self, purpose, packet, key):
        cache = self.state.setdefault('reviews', {})
        fingerprint = canonical_sha256([purpose, packet])
        if key in cache and cache[key]['fingerprint'] != fingerprint:
            raise Halt('review_evidence_changed: '+key)
        if key not in cache:
            self.state['inflight'] = {'key': key, 'at': time.time()}
            self.save()
            result = self.reviewer(self.repo, self.actor, purpose, packet,
                                   self.state.get('vision_provider_id') if purpose == 'visual' else (self.state.get('review_provider_id') if purpose.startswith('watchdog') else None))
            self.check_stop()
            cache[key] = {'fingerprint': fingerprint, 'result': result}
            self.state.pop('inflight', None)
            self.save()
        return cache[key]['result']

    def items(self):
        return ingestion.get_task_items(self.conn, self.actor, self.state['paper_task'], limit=500)

    def layout(self, task_id):
        task = ingestion.get_task(self.conn, self.actor, task_id)
        row = self.conn.execute('select layout_key,manifest_key from document_conversions where id=?', (task['conversion_id'],)).fetchone()
        if row is None:
            raise Halt('conversion_missing')
        return json.loads(self.store.read(row['layout_key'], max_bytes=100*1024*1024)), json.loads(self.store.read(row['manifest_key'], max_bytes=100*1024*1024))

    def packet(self, item, include_answers=False):
        layout, manifest = self.layout(self.state['paper_task'])
        spans = item['document']['source_spans']
        ids = {s['block_id'] for s in spans}
        blocks = [b for b in layout['blocks'] if b['id'] in ids]
        if not blocks:
            raise Halt('source_blocks_missing')
        result = {'source': blocks, 'document': item['document'],
                  'issues': [{'id': canonical_sha256(i)[:20], 'issue': i} for i in item['document']['issues']],
                  'conversion_issues': [issue for issue in manifest.get('issues', []) if issue.get('field') in ids or issue.get('field') in (None, 'source', 'document')],
                  'visual_evidence_available': False}
        result['original_ooxml'] = raw_question_xml(self.state['paper']['files']['paper']['path'], item['document']['number'])
        if include_answers:
            result['answer_original_ooxml'] = raw_question_xml(self.state['paper']['files']['answers']['path'], item['document']['number'])
            groups = ingestion._answer_groups(self.conn, self.actor, self.state['answers_task'], self.state['db'])
            group = groups.get(item['document']['number'], [])
            if len(group) != 1:
                raise Halt('answer_source_not_unique')
            result['answer_source'] = group[0]
        return result

    def upload(self, role):
        info = self.state['paper']['files'][role]
        path = Path(info['path'])
        if digest_file(path) != info['sha256']:
            raise Halt('source_file_changed')
        payload = {'name': path.name, 'size': path.stat().st_size, 'sha256': info['sha256'],
                   'title': self.state['paper']['title'], 'role': role, 'request_key': self.key('upload-'+role),
                   'import_mode': 'paper', 'tagging_policy': 'question_bank'}
        if role == 'answers':
            payload['original_paper_id'] = ingestion.get_task(self.conn, self.actor, self.state['paper_task'])['original_paper_id']
        upload = ingestion.create_upload(self.conn, self.actor, payload, self.state['db'])
        if upload['state'] == 'uploading':
            received = set(upload['received_parts'])
            with path.open('rb') as stream:
                for index in range(upload['total_parts']):
                    chunk = stream.read(upload['chunk_size'])
                    if index in received:
                        continue
                    self.check_stop()
                    ingestion.store_upload_part(self.conn, self.actor, upload['upload_id'], {
                        'index': index, 'sha256': hashlib.sha256(chunk).hexdigest(),
                        'data_base64': base64.b64encode(chunk).decode()}, self.state['db'])
        return ingestion.complete_upload(self.conn, self.actor, upload['upload_id'], self.state['db'])['task_id']

    def blank_original_preamble_asset(self, task, asset_id):
        """Only ignore a visually blank asset when its original DOCX pixels match."""
        from PIL import Image
        row = self.conn.execute("""select a.storage_key,a.sha256,r.source_locator_json from document_assets a
            join conversion_asset_refs r on r.asset_id=a.id where r.conversion_id=? and a.id=? and a.school_id=?""",
            (task['conversion_id'], asset_id, self.actor['school_id'])).fetchall()
        if len(row) != 1:
            return False
        locator = json.loads(row[0]['source_locator_json'])
        relid = locator.get('relationship_id')
        path = self.state['paper']['files']['paper']['path']
        if not relid or Path(path).suffix.lower() != '.docx':
            return False
        with zipfile.ZipFile(path) as archive:
            rels = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
            matches = [x for x in rels if x.get('Id') == relid and x.get('TargetMode') != 'External']
            if len(matches) != 1 or not matches[0].get('Type', '').endswith('/image'):
                return False
            target = posixpath.normpath('word/'+matches[0].get('Target', ''))
            if not target.startswith('word/media/') or not target.lower().endswith('.png'):
                return False
            raw = archive.read(target)
        original = Image.open(io.BytesIO(raw)).convert('RGBA')
        converted = Image.open(io.BytesIO(self.store.read(row[0]['storage_key'],row[0]['sha256'],max_bytes=25*1024*1024))).convert('RGBA')
        if original.size != converted.size or original.tobytes() != converted.tobytes():
            return False
        ranges = original.getextrema()
        if any(low < 254 for low,high in ranges[:3]) or ranges[3] != (255,255):
            return False
        evidence = {'asset_id':asset_id,'asset_sha256':row[0]['sha256'],
                    'original_sha256':hashlib.sha256(raw).hexdigest(),'original_part':target,
                    'rgba_extrema':ranges,'reason':'原卷标题段落的源图与转换图像素一致且全白'}
        self.state.setdefault('blank_preamble_evidence', {})[asset_id] = evidence
        return True

    def validate_inventory(self):
        items = self.items()
        numbers = [i['document']['number'] for i in items]
        if not numbers or numbers != self.state['paper']['expected_numbers'] or len(numbers) != len(set(numbers)):
            raise Halt('question_number_or_order_mismatch: '+','.join(numbers))
        layout, manifest = self.layout(self.state['paper_task'])
        task = ingestion.get_task(self.conn, self.actor, self.state['paper_task'])
        if any(i.get('severity') == 'blocking' and i.get('state') != 'resolved' for i in manifest.get('issues', [])):
            raise Halt('blocking_conversion_issue')
        assigned = {s['block_id'] for item in items for s in item['document']['source_spans']}
        # Source figures must not silently disappear from all candidates.
        for block in layout['blocks']:
            if block.get('asset_ids') and block['id'] not in assigned:
                title_context = block.get('markdown', '') + (layout['blocks'][1].get('markdown', '') if len(layout['blocks']) > 1 else '')
                is_first_title = (block is layout['blocks'][0] and block.get('source_locator', {}).get('paragraph_index') == 1
                                  and bool(re.search(r'2026.{0,30}(?:考试|物理)',title_context)))
                if not is_first_title or not all(self.blank_original_preamble_asset(task, aid) for aid in block['asset_ids']):
                    raise Halt('unassigned_source_figure: '+block['id'])
        for item in items:
            d = validate_question_document(item['document'])
            for aid in d['asset_refs']:
                row = self.conn.execute('select storage_key,sha256 from document_assets where id=? and school_id=?', (aid, self.actor['school_id'])).fetchone()
                if row is None:
                    raise Halt('asset_missing')
                self.store.read(row['storage_key'], row['sha256'], max_bytes=25*1024*1024)
        for item in items:
            for phase in ('paper', 'answers'):
                saved = self.state.get('reviewed_'+phase, {}).get(item['id'])
                if saved and saved != canonical_sha256(item['document']):
                    raise Halt('reviewed_document_changed')
            if item['published_question_ids']:
                for qid in item['published_question_ids']:
                    binding = question_bank.content(self.conn, qid, self.actor['school_id'])
                    if not binding or canonical_sha256(binding['document']) != canonical_sha256(item['document']):
                        raise Halt('published_content_readback_mismatch')
        return items

    def review_one(self, stage):
        items = self.validate_inventory()
        done = self.state.setdefault('reviewed_'+stage, {})
        for item in items:
            if item['id'] in done:
                if canonical_sha256(item['document']) != done[item['id']]:
                    raise Halt('reviewed_document_changed')
                continue
            name = stage+'-'+item['id']
            # Recover an already committed save without re-reading/re-prompting newer evidence.
            action = self.state.get('actions', {}).get(name)
            if action is None:
                result = self.call_model(stage, self.packet(item, stage == 'answers'), name)
                document = apply_patches(item['document'], result, stage)
                unresolved = [i for i in item['document']['issues'] if i.get('severity') in ('blocking','review') and i.get('state') != 'resolved']
                resolved = result.get('resolved_issue_ids', [])
                if not isinstance(resolved, list) or any(not isinstance(x, str) for x in resolved):
                    raise Halt('invalid_issue_resolution')
                # Missing answers are expected at the paper stage, never erase them there.
                applicable = [i for i in unresolved if stage == 'answers' or 'answer' not in i.get('code', '')]
                if any(canonical_sha256(i)[:20] not in resolved for i in applicable):
                    raise Halt('unresolved_parser_issue')
                payload = {'request_key': self.key(name), 'expected_revision': item['review_revision'],
                           'document': document, 'resolved_issue_ids': resolved,
                           'resolution_note': '自动流程逐题核对：'+result['reason'][:900]}
            else:
                payload = action['payload']
            saved = self.operation(name, payload, lambda p: ingestion.save_candidate(self.conn, self.actor, item['id'], p))
            done[item['id']] = canonical_sha256(saved['document'])
            self.save()
            return False
        return True

    def visual_receipt(self, item):
        if not self.state.get('vision_provider_id'):
            raise Halt('visual_review_required: '+item['id'])
        packet = self.packet(item, True)
        images = []
        for role in ('paper','answers'):
            task = ingestion.get_task(self.conn, self.actor, self.state[role+'_task'])
            conversion = self.conn.execute('select layout_key from document_conversions where id=?', (task['conversion_id'],)).fetchone()
            preview_key = str(Path(conversion['layout_key']).parent/'preview.pdf')
            preview = self.store.read(preview_key, max_bytes=100*1024*1024)
            with tempfile.TemporaryDirectory(prefix='hsp-visual-') as temp:
                pdf = Path(temp)/'source.pdf'; pdf.write_bytes(preview)
                info = subprocess.run(['pdfinfo', str(pdf)], capture_output=True, timeout=30, check=True).stdout.decode()
                pages = re.search(r'^Pages:\s+(\d+)', info, re.M)
                if not pages or int(pages[1]) > 12:
                    raise Halt('visual_source_page_limit')
                subprocess.run(['pdftoppm','-scale-to','1400','-png',str(pdf),str(Path(temp)/'page')],
                               capture_output=True, timeout=60, check=True)
                for page in sorted(Path(temp).glob('page-*.png')):
                    images.append('data:image/png;base64,'+base64.b64encode(page.read_bytes()).decode())
        packet.update(source_page_images=images, visual_evidence_available=True,
                      source_page_order='paper pages, then answers pages')
        verdict = self.call_model('visual', packet, 'visual-'+item['id'])
        required = ('number','stem','options','formulas','figures','children','answer')
        if verdict.get('verdict') != 'pass' or not isinstance(verdict.get('checks'), dict) or any(verdict['checks'].get(k) is not True for k in required) or verdict.get('patches') or verdict.get('resolved_issue_ids'):
            raise Halt('visual_quality_failure: '+str(verdict.get('reason', 'incomplete checks')))
        receipt = {'paper_sha256':self.state['paper']['files']['paper']['sha256'],
                   'answers_sha256':self.state['paper']['files']['answers']['sha256'],
                   'document_sha256':canonical_sha256(item['document']), 'reviewer':verdict.get('model'),
                   'note':verdict.get('reason'), 'at':time.time(), 'kind':'model_visual_review'}
        self.state.setdefault('visual_receipts', {})[item['id']] = receipt
        self.save()
        return receipt

    def watchdog_one(self, tags=False):
        items = self.validate_inventory()
        if 'sample' not in self.state:
            selected = set(sample_ids(items, self.state['run_id']))
            for item in items:
                raw = raw_question_xml(self.state['paper']['files']['paper']['path'], item['document']['number'])
                if raw and any(b['embedded_objects'] or b['math_xml'] for b in raw):
                    selected.add(item['id'])
            self.state['sample'] = [i['id'] for i in items if i['id'] in selected]
            self.save()
        sample = self.state['sample']
        cache_name = 'watchdog_tags' if tags else 'watchdog_content'
        done = self.state.setdefault(cache_name+'_done', [])
        for item in items:
            if item['id'] not in sample or item['id'] in done:
                continue
            packet = self.packet(item, True)
            packet.pop('issues', None)
            packet['response_contract'] = {'audit_only': True, 'patches': [], 'resolved_issue_ids': [],
                'instruction': '本阶段只读核对，已有告警状态由前序步骤处理；这两个数组必须为空。'}
            if tags:
                packet['tag_proposals'] = {qid: self.state['tag_proposals'][qid] for qid in item['published_question_ids']}
                catalog = question_bank.taxonomy(self.repo, self.actor)
                # Only selected IDs with their authoritative names/paths. Passing the
                # whole ontology led a real watchdog to confuse Newton's law with momentum.
                packet['tag_catalog'] = {family: [{k: t[k] for k in ('id','name','path_text')} for t in values
                    if any(t['id'] in proposal[family] for proposal in packet['tag_proposals'].values())]
                    for family, values in catalog.items()}
            # Producer opinions are deliberately absent. Raw source, current result only.
            verdict = self.call_model(cache_name, packet, cache_name+'-'+item['id'])
            required = ['number','stem','options','formulas','children','answer']+(['tags'] if tags else [])
            if verdict.get('verdict') != 'pass':
                raise Halt('watchdog_quality_failure: '+str(verdict.get('reason', 'missing verdict')))
            if verdict.get('patches') != [] or verdict.get('resolved_issue_ids') != []:
                raise Halt('watchdog_contract_failure: read_only_arrays_must_be_empty')
            if not isinstance(verdict.get('checks'), dict) or any(verdict['checks'].get(k) is not True for k in required):
                raise Halt('watchdog_checks_incomplete')
            raw = packet.get('original_ooxml') or []
            if item['document']['asset_refs'] or '$' in json.dumps(item['document'], ensure_ascii=False) or any(b['embedded_objects'] or b['math_xml'] for b in raw):
                # A text model cannot grant a visual pass. An independently recorded
                # visual receipt must bind BOTH source hashes and the reviewed document.
                receipt = self.state.get('visual_receipts', {}).get(item['id'])
                expected = {'paper_sha256': self.state['paper']['files']['paper']['sha256'],
                            'answers_sha256': self.state['paper']['files']['answers']['sha256'],
                            'document_sha256': canonical_sha256(item['document'])}
                if not receipt or any(receipt.get(k) != v for k,v in expected.items()):
                    self.visual_receipt(item)
            done.append(item['id'])
            self.save()
            return False
        return True

    def tick(self):
        self.check_stop()
        stage = self.state['stage']
        self.state['heartbeat_at'] = time.time()
        self.save()
        finished = True
        if stage in ('upload_paper', 'upload_answers'):
            role = 'paper' if stage == 'upload_paper' else 'answers'
            self.state[role+'_task'] = self.upload(role)
            self.state['wait_started_at'] = time.time()
        elif stage in ('wait_paper', 'wait_answers'):
            role = 'paper' if stage == 'wait_paper' else 'answers'
            task = ingestion.get_task(self.conn, self.actor, self.state[role+'_task'])
            if task['status'] in ('failed', 'cancelled'):
                raise Halt('conversion_failed: '+task['error_code'])
            finished = task['status'] in ('parsed', 'partially_parsed')
            if not finished and time.time()-self.state['wait_started_at'] > 1800:
                raise Halt('conversion_wait_timeout')
        elif stage == 'review_paper':
            finished = self.review_one('paper')
        elif stage == 'confirm_paper':
            items = self.validate_inventory()
            refs = [{'id': i['id'], 'expected_revision': i['review_revision']} for i in items]
            self.operation(stage, {'request_key': self.key(stage), 'items': refs}, lambda p: ingestion.confirm_paper_review(self.conn, self.actor, self.state['paper_task'], p))
        elif stage == 'attach_answers':
            self.state.pop('reviewed_paper', None)
            action = self.state.get('actions', {}).get(stage)
            if action:
                payload = action['payload']
            else:
                preview = ingestion.attach_answers(self.conn, self.actor, self.state['paper_task'], {'answer_task_id': self.state['answers_task']}, self.state['db'])
                if preview['issues'] or len(preview['matches']) != len(self.items()):
                    raise Halt('answer_mapping_incomplete_or_ambiguous')
                payload = {'answer_task_id': self.state['answers_task'], 'request_key': self.key(stage),
                           'mappings': [{k: m[k] for k in ('item_id','answer_number','expected_revision')} for m in preview['matches']]}
            self.operation(stage, payload, lambda p: ingestion.attach_answers(self.conn, self.actor, self.state['paper_task'], p, self.state['db']))
            # Attaching answers intentionally invalidates the earlier document hashes.
            self.state.pop('reviewed_paper', None)
        elif stage == 'review_answers':
            finished = self.review_one('answers')
        elif stage == 'watchdog_content':
            finished = self.watchdog_one()
        elif stage == 'publish':
            items = self.validate_inventory()
            if set(self.state.get('watchdog_content_done', [])) != set(self.state['sample']):
                raise Halt('watchdog_not_complete')
            # Do not use reviewed=True: it would auto-resolve every remaining warning.
            payload = {'request_key': self.key(stage), 'items': [{'id': i['id'], 'expected_revision': i['review_revision']} for i in items]}
            self.state['publication'] = self.operation(stage, payload, lambda p: ingestion.confirm_candidates(self.conn, self.actor, self.state['paper_task'], p))
        elif stage == 'generate_tags':
            proposals = self.state.setdefault('tag_proposals', {})
            qids = [q for i in self.items() for q in i['published_question_ids']]
            finished = True
            for qid in qids:
                if qid in proposals:
                    continue
                q = self.repo.get_question(qid)
                revision = question_bank.tag_revision(self.conn, qid)
                if revision != 0:
                    raise Halt('manual_tags_preserved')
                candidate = self.repo.generate_llm_candidates(self.actor['id'], qid)
                if candidate['model_version'] == 'rules-only':
                    raise Halt('real_model_required')
                selected = {family: [v['id'] for v in candidate[family+'_tags'] if v['confidence'] >= 0.8] for family in ('knowledge','ability','literacy')}
                if not selected['knowledge']:
                    raise Halt('knowledge_tags_below_threshold')
                proposals[qid] = {'question_id': qid, 'question_version': q['version'], 'expected_revision': revision,
                                  **selected, 'candidate_id': candidate['id']}
                finished = False
                break
        elif stage == 'watchdog_tags':
            finished = self.watchdog_one(True)
        elif stage == 'adopt_tags':
            entries = [{k: v for k,v in e.items() if k != 'candidate_id'} for e in self.state['tag_proposals'].values()]
            self.operation(stage, {'entries': entries}, self.adopt)
        elif stage == 'verify':
            items = self.validate_inventory()
            if any(not i['published_question_ids'] for i in items):
                raise Halt('publication_missing')
            for qid, proposal in self.state['tag_proposals'].items():
                if self.repo.get_question(qid)['version'] != proposal['question_version'] or question_bank.tag_revision(self.conn, qid) != proposal['expected_revision']+1:
                    raise Halt('final_version_changed')
                actual = self.repo.tags_for_question(qid)
                for family in ('knowledge','ability','literacy'):
                    if sorted(t['tag_id'] for t in actual if t['tag_type'] == family) != sorted(proposal[family]):
                        raise Halt('final_tag_readback_mismatch')
            self.state['report'] = {'main_questions': len(items), 'answer_units': sum(len(i['published_question_ids']) for i in items),
                'sample': self.state['sample'], 'partial': self.state['paper'].get('partial', False),
                'scope': 'question_bank_only', 'visual_receipts': len(self.state.get('visual_receipts', {})),
                'watchdog_independence': 'separate_stateless_request; same provider unless explicitly configured'}
            atomic_json(self.directory/'report.json', self.state['report'])
        elif stage == 'complete':
            return stage
        else:
            raise Halt('unknown_stage')
        self.check_stop()
        if finished:
            self.state['stage'] = STAGES[STAGES.index(stage)+1]
        self.save()
        return self.state['stage']

    def adopt(self, payload):
        # save_tags is atomic but has no request key. Recover only an exact +1
        # revision AND identical labels, never adopt over concurrent teacher edits.
        matches = []
        for entry in payload['entries']:
            actual = self.repo.tags_for_question(entry['question_id'])
            same = all(sorted(t['tag_id'] for t in actual if t['tag_type'] == family) == sorted(entry[family]) for family in ('knowledge','ability','literacy'))
            matches.append(same and question_bank.tag_revision(self.conn, entry['question_id']) == entry['expected_revision']+1 and self.repo.get_question(entry['question_id'])['version'] == entry['question_version'])
        if all(matches):
            return {'recovered': True}
        if any(matches):
            raise Halt('tag_atomicity_or_concurrent_edit')
        return question_bank.save_tags(self.repo, self.actor, payload)


def create_run(directory, db, actor_id, paper, review_provider_id=None, vision_provider_id=None):
    if paper.get('issues') or not paper.get('expected_numbers') or set(paper.get('files', {})) != {'paper','answers'}:
        raise Halt('manifest_requires_unambiguous_paper_answers_and_expected_numbers')
    for info in paper['files'].values():
        if digest_file(info['path']) != info['sha256']:
            raise Halt('source_file_changed')
    if not Path(db).is_file():
        raise Halt('database_missing')
    conn = connect(db)
    try:
        actor = PhysicsRepository(conn)._require_question_bank_actor(actor_id)
        school_id = actor['school_id']
    finally:
        conn.close()
    directory = Path(directory)
    with locked(directory):
        if (directory/'state.json').exists():
            raise Halt('run_already_exists')
        atomic_json(directory/'state.json', {'version': VERSION, 'run_id': os.urandom(16).hex(),
            'db': str(Path(db).resolve()), 'actor_id': actor_id, 'school_id': school_id, 'paper': paper,
            'review_provider_id': review_provider_id, 'vision_provider_id': vision_provider_id, 'stage': STAGES[0], 'created_at': time.time(), 'updated_at': time.time()})


def monitor(directory, stale_seconds=600):
    """Separate watchdog; no runner lock, so it can stop a stalled model request."""
    directory = Path(directory)
    state = json.loads((directory/'state.json').read_text())
    reason = None
    if state['stage'] != 'complete' and time.time()-state['updated_at'] > stale_seconds:
        reason = 'runner_stale'
    for role, info in state['paper']['files'].items():
        if not Path(info['path']).is_file() or digest_file(info['path']) != info['sha256']:
            reason = 'source_file_changed:'+role
    if state['stage'] not in STAGES:
        reason = 'unknown_stage'
    if state['stage'] in ('generate_tags','watchdog_tags','adopt_tags','verify','complete'):
        conn = sqlite3.connect('file:'+str(Path(state['db']).resolve())+'?mode=ro', uri=True)
        try:
            for item_id, fingerprint in state.get('reviewed_answers', {}).items():
                rows = conn.execute("""select r.document_json from question_content_groups g
                    join question_content_revisions r on r.id=g.current_revision_id
                    where g.source_item_id=? and g.school_id=?""", (item_id, state['school_id'])).fetchall()
                if len(rows) != 1 or canonical_sha256(json.loads(rows[0][0])) != fingerprint:
                    reason = 'published_content_drift:'+item_id
            if state['stage'] == 'complete':
                for qid, proposal in state.get('tag_proposals', {}).items():
                    rows = conn.execute('select tag_type,tag_id from question_tags where question_id=? and enabled=1', (qid,)).fetchall()
                    if any(sorted(t[1] for t in rows if t[0] == family) != sorted(proposal[family]) for family in ('knowledge','ability','literacy')):
                        reason = 'published_tags_drift:'+qid
        finally:
            conn.close()
    if reason:
        atomic_json(directory/'STOP.json', {'reason': reason, 'at': time.time(), 'stage': state['stage']}, exclusive=True)
    stopped = (directory/'STOP.json').exists()
    if stopped:
        reason = json.loads((directory/'STOP.json').read_text()).get('reason')
    return {'stage': state['stage'], 'stopped': stopped, 'reason': reason}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('inventory'); p.add_argument('roots', nargs='+'); p.add_argument('--output', required=True)
    p = sub.add_parser('create'); p.add_argument('--manifest', required=True); p.add_argument('--index', type=int, required=True)
    p.add_argument('--db', required=True); p.add_argument('--actor', required=True); p.add_argument('--run', required=True); p.add_argument('--review-provider-id'); p.add_argument('--vision-provider-id')
    p = sub.add_parser('run'); p.add_argument('--run', required=True); p.add_argument('--steps', type=int, default=1); p.add_argument('--interval', type=float, default=2)
    p = sub.add_parser('watchdog'); p.add_argument('--run', required=True); p.add_argument('--stale-seconds', type=int, default=600)
    p = sub.add_parser('status'); p.add_argument('--run', required=True)
    p = sub.add_parser('resume'); p.add_argument('--run', required=True); p.add_argument('--reason', required=True)
    p = sub.add_parser('visual-receipt'); p.add_argument('--run', required=True); p.add_argument('--receipt', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'inventory':
            result = inventory(args.roots); atomic_json(args.output, result)
            print(json.dumps({'papers': len(result['papers']), 'ready': sum(not p['issues'] for p in result['papers']), 'output': args.output})); return
        if args.command == 'create':
            manifest = json.loads(Path(args.manifest).read_text())
            if args.index < 0:
                raise Halt('invalid_manifest_index')
            create_run(args.run, args.db, args.actor, manifest['papers'][args.index], args.review_provider_id, args.vision_provider_id)
            print('created'); return
        if args.command == 'watchdog':
            result = monitor(args.run, args.stale_seconds); print(json.dumps(result)); return 2 if result['stopped'] else 0
        if args.command == 'status':
            state = json.loads((Path(args.run)/'state.json').read_text())
            print(json.dumps({'stage': state['stage'], 'title': state['paper']['title'], 'stopped': (Path(args.run)/'STOP.json').exists(), 'updated_at': state['updated_at']}, ensure_ascii=False)); return
        with locked(args.run):
            pipeline = Pipeline(args.run)
            try:
                if args.command == 'resume':
                    if len(args.reason.strip()) < 8:
                        raise Halt('resume_reason_required')
                    pipeline.state.setdefault('resumes', []).append({'at': time.time(), 'reason': args.reason})
                    reviews = pipeline.state.get('reviews', {})
                    prefix = {'review_paper': 'paper-', 'review_answers': 'answers-',
                        'watchdog_content': 'watchdog_content-', 'watchdog_tags': 'watchdog_tags-'}.get(pipeline.state['stage'])
                    failed = {k: v for k,v in reviews.items() if v['result'].get('verdict') != 'pass' or (prefix and k.startswith(prefix))}
                    if failed:
                        pipeline.state.setdefault('rejected_reviews', []).append(failed)
                        for key in failed:
                            del reviews[key]
                    pipeline.save()
                    (Path(args.run)/'STOP.json').unlink(missing_ok=True)
                    print('resumed'); return
                if args.command == 'visual-receipt':
                    receipt = json.loads(Path(args.receipt).read_text())
                    item = next((i for i in pipeline.items() if i['id'] == receipt.get('item_id')), None)
                    if not item or not isinstance(receipt.get('reviewer'), str) or len(receipt.get('note', '').strip()) < 8:
                        raise Halt('invalid_visual_receipt')
                    expected = {'paper_sha256': pipeline.state['paper']['files']['paper']['sha256'],
                                'answers_sha256': pipeline.state['paper']['files']['answers']['sha256'],
                                'document_sha256': canonical_sha256(item['document'])}
                    if any(receipt.get(k) != v for k,v in expected.items()):
                        raise Halt('visual_receipt_hash_mismatch')
                    pipeline.state.setdefault('visual_receipts', {})[item['id']] = receipt
                    pipeline.save(); print('receipt saved'); return
                for _ in range(max(1, min(args.steps, 10000))):
                    stage = pipeline.tick()
                    print(json.dumps({'stage': stage}), flush=True)
                    if stage == 'complete':
                        break
                    time.sleep(max(0, min(args.interval, 30)))
            except Exception as exc:
                pipeline.conn.rollback()
                # Persist a safe error category, never exception strings from a provider/DB.
                reason = str(exc) if isinstance(exc, Halt) else type(exc).__name__+':'+str(getattr(exc, 'code', 'step_failed'))
                pipeline.halt(reason)
                print(json.dumps({'stopped': True, 'stage': pipeline.state['stage'], 'reason': reason}, ensure_ascii=False))
                return 2
            finally:
                pipeline.close()
    except (Halt, DomainError, OSError, ValueError, KeyError, IndexError) as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, Halt) else type(exc).__name__}, ensure_ascii=False)); return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
