"""Prepare isolated Log Sim projects from verified retained native executions.

Default: one rehearsal project. --participants 3..5: one Engineer project per
person, without invitations, native executions, or existing service changes.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlparse, quote
from uuid import uuid4

import httpx
from mcap.writer import Writer
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from ade2e.experiment_models import ExperimentExecutionRow
from ade2e.log_sim import carla_planning_frames
from ade2e.object_demo import _execution
from ade2e.object_story import read_verified_scene
from ade2e.scene_sequences import EGO_SCHEMA, OBJECT_SCHEMA
from ade2e.service import canonical_bytes, utcnow

PACK_IDS = ('stop-resume', 'lead-brake', 'following-gap', 'cut-in', 'intersection')


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temporary.replace(path)


def write_token(path, value):
    """Private from its first byte; bearer tokens never go into receipts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(value)


def retained_sources(workspace, jobs):
    """Read original SQLite/artifacts without initializing or migrating them."""
    if not isinstance(jobs, dict) or set(jobs) != set(PACK_IDS):
        raise ValueError('Select all five representative pack executions explicitly')
    workspace = Path(workspace).resolve()
    database_path = workspace / 'platform.db'
    if not database_path.is_file():
        raise ValueError('Retained source workspace database is missing')
    uri = 'file:' + quote(str(database_path), safe='/') + '?mode=ro'
    engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(uri, uri=True))
    db = SimpleNamespace(experiment_dir=workspace / 'experiments')
    result = []
    try:
        for pack in PACK_IDS:
            job = jobs[pack]
            with Session(engine) as session:
                session.execute(text('BEGIN'))
                execution = session.get(ExperimentExecutionRow, job)
                if execution is None:
                    raise ValueError(f'Retained execution missing for {pack}: {job}')
                side, snapshot = _execution(session, execution.project_id, job)
                scene, receipt = read_verified_scene(db, session, side, snapshot)
            count = len(scene['frames'])
            frames = carla_planning_frames(scene | {'frames': scene['frames'][:512]})
            if len(frames) < 2:
                raise ValueError(f'{pack} needs at least two retained observations')
            provenance = receipt | {
                'original_frame_count': count, 'selected_first_frame': 0,
                'selected_frame_count': len(frames), 'fidelity': side['execution']['fidelity'],
                'adapter_version': side['execution']['adapter_version'], 'seed': snapshot['source']['seed'],
                'coordinate_conversion': 'bbox-center then Y/yaw reflection', 'sensor_validation': False,
            }
            out = io.BytesIO()
            writer = Writer(out)
            writer.start(profile='axiom-retained-native-log-beta/1')
            es = writer.register_schema('axiom.PlanningEgo.v1', 'jsonschema', canonical_bytes(EGO_SCHEMA))
            oschema = writer.register_schema('axiom.ObjectTracks.v1', 'jsonschema', canonical_bytes(OBJECT_SCHEMA))
            ec = writer.register_channel('/planning/ego', 'json', es)
            oc = writer.register_channel('/planning/objects', 'json', oschema)
            writer.add_metadata('axiom.retained_native_source', {'lineage_json': canonical_bytes(provenance).decode()})
            for index, (ego, objects) in enumerate(frames):
                timestamp = int(ego['timestamp_ns'])
                writer.add_message(ec, timestamp, canonical_bytes(ego), timestamp, index)
                writer.add_message(oc, timestamp, canonical_bytes(objects), timestamp, index)
            writer.finish()
            raw = out.getvalue()
            result.append({'id': pack, 'raw': raw, 'mcap_sha256': hashlib.sha256(raw).hexdigest(),
                'ego_channel_id': ec, 'objects_channel_id': oc, 'provenance': provenance})
    finally:
        engine.dispose()
    return result


def participant_labels(count, supplied=None):
    if count is None:
        if supplied:
            raise ValueError('--participant-label requires --participants')
        return ['Log beta engineer']
    if count not in (3, 4, 5):
        raise ValueError('Choose 3..5 internal participants')
    values = supplied or [f'Log beta engineer {index + 1}' for index in range(count)]
    if len(values) != count or len(set(values)) != count or any(not value.strip() or len(value) > 120 for value in values):
        raise ValueError('Supply one distinct nonempty label (max 120 characters) per participant')
    return values


def prepare(runtime, api_url, sources, *, participants=None, labels=None, client=None):
    """Resume a receipt; check every ready project against live source bindings."""
    if urlparse(api_url).hostname != '127.0.0.1':
        raise ValueError('This preparation helper is local-only')
    labels = participant_labels(participants, labels)
    if len(sources) != len(PACK_IDS) or {source['id'] for source in sources} != set(PACK_IDS):
        raise ValueError('All five verified retained sources are required before creating projects')
    for source in sources:
        if hashlib.sha256(source['raw']).hexdigest() != source['mcap_sha256']:
            raise ValueError(f"Prepared MCAP bytes changed for {source['id']}")
    root = Path(runtime).resolve()
    target_api = api_url.rstrip('/') + '/api/v1'
    path = root / ('participants.json' if participants else 'prepared.json')
    pins = [{'id': source['id'], 'mcap_sha256': source['mcap_sha256'], 'provenance': source['provenance']}
            for source in sources]
    fingerprint = hashlib.sha256(canonical_bytes(pins)).hexdigest()
    if path.exists():
        receipt = json.loads(path.read_text())
        if receipt['target_api'] != target_api or receipt.get('source_fingerprint', fingerprint) != fingerprint:
            raise ValueError('Preparation receipt pins another source or API; use a separate runtime')
        entries = receipt['participants'] if participants else [receipt]
        if len(entries) != len(labels) or any(entry.get('label', label) != label for entry, label in zip(entries, labels)):
            raise ValueError('Participant count or labels differ from this saved preparation')
        # Older single-project receipts pin the exact retained provenance too.
        for entry in entries:
            for pack in entry.get('packs', []):
                match = next((source for source in sources if source['id'] == pack['id']), None)
                if match is None or match['provenance'] != pack['provenance']:
                    raise ValueError('Saved pack provenance differs from the requested source')
    else:
        receipt = {'schema_version': 'log-beta-participants/1' if participants else 'log-beta-preparation/2',
            'target_api': target_api, 'preparation_id': uuid4().hex[:12], 'source_fingerprint': fingerprint}
        entries = [{'label': label, 'packs': [], 'token_file': f'access/participant-{index + 1}-token' if participants else 'access/tester-token'}
                   for index, label in enumerate(labels)]
        if participants:
            receipt['participants'] = entries
        else:
            receipt.update(entries[0])
            entries = [receipt]
    root.mkdir(parents=True, exist_ok=True)
    receipt.setdefault('preparation_id', uuid4().hex[:12])
    receipt['source_fingerprint'] = fingerprint
    receipt['ready'] = False
    for index, entry in enumerate(entries):
        entry.setdefault('label', labels[index])
        entry.setdefault('token_file', f'access/participant-{index + 1}-token' if participants else 'access/tester-token')
        entry['ready'] = False
    write_json(path, receipt)
    for source in sources:
        mcap_path = root / (source['id'] + '.mcap')
        if mcap_path.exists() and hashlib.sha256(mcap_path.read_bytes()).hexdigest() != source['mcap_sha256']:
            raise ValueError(f'Existing MCAP differs from verified source: {mcap_path.name}')
        if not mcap_path.exists():
            mcap_path.write_bytes(source['raw'])
    owns_client = client is None
    if owns_client:
        operator = (root / 'access/operator-token').read_text().strip()
        client = httpx.Client(base_url=target_api, headers={'Authorization': 'Bearer ' + operator}, timeout=120)

    def call(method, route, **kwargs):
        response = client.request(method, route, **kwargs)
        response.raise_for_status()
        return response.json()

    try:
        for entry in entries:
            project_name = f"Log beta {receipt['preparation_id']} · {entry['label']}"
            if not entry.get('project_id'):
                matches = [project for project in call('GET', '/projects')['items'] if project['name'] == project_name]
                if len(matches) > 1:
                    raise ValueError('Multiple matching projects; resolve the preparation receipt before resuming')
                project = matches[0] if matches else call('POST', '/projects', json={'name': project_name})
                entry['project_id'] = project['id']
                write_json(path, receipt)
            project_id = entry['project_id']
            token_path = root / entry['token_file']
            if not token_path.resolve().is_relative_to((root / 'access').resolve()):
                raise ValueError('Token file must stay within this runtime access directory')
            if not token_path.exists():
                live_tokens = call('GET', f'/projects/{project_id}/tokens')['items']
                if any(token['user_label'] == entry['label'] and not token.get('revoked_at') for token in live_tokens):
                    raise ValueError('A participant token exists but its private file is missing; recover or revoke that token before resuming')
                token = call('POST', f'/projects/{project_id}/tokens', json={'user_label': entry['label'], 'role': 'Engineer'})
                write_token(token_path, token['token'])
                entry['token_id'] = token['id']
                write_json(path, receipt)
            token_path.chmod(0o600)
            who = call('GET', '/team/whoami', headers={'Authorization': 'Bearer ' + token_path.read_text().strip()})
            if who['mode'] != 'project' or who['project_id'] != project_id or who['role'] != 'Engineer':
                raise ValueError('Saved participant token does not provide this project Engineer access')
            for source in sources:
                pack_id = source['id']
                existing = next((pack for pack in entry['packs'] if pack['id'] == pack_id), None)
                if existing:
                    detail = call('GET', '/log-sim/sources/' + existing['source_id'], params={'project_id': project_id})
                    if detail['source_hash'] != existing['source_hash']:
                        raise ValueError(f'Saved source hash changed for {pack_id}')
                    continue
                upload = call('POST', '/log-sim/uploads', params={'project_id': project_id, 'name': pack_id + ' retained CARLA.mcap'},
                    content=source['raw'], headers={'Content-Type': 'application/octet-stream'})
                body = {'project_id': project_id, 'recording_id': upload['id'], 'title': pack_id + ' · retained CARLA object trace',
                    'ego_channel_id': source['ego_channel_id'], 'objects_channel_id': source['objects_channel_id'],
                    'sample_count': source['provenance']['selected_frame_count']}
                preview = call('POST', '/log-sim/inputs/inspect', json=body)
                saved = call('POST', '/log-sim/inputs', json=body | {'preview_hash': preview['preview_hash']})
                detail = call('GET', '/log-sim/sources/' + saved['id'], params={'project_id': project_id})
                provenance = source['provenance']
                note = (f"Verified retained native CARLA execution {provenance['execution_id']}; first "
                    f"{provenance['selected_frame_count']} of {provenance['original_frame_count']} recorded frames. "
                    'Object truth, no camera or sensor validation; uploaded origin remains unverified by the generic importer.')
                call('POST', f'/log-sim/packs/{pack_id}/binding', json={'project_id': project_id,
                    'source_id': saved['id'], 'source_hash': detail['source_hash'], 'note': note})
                entry['packs'].append({'id': pack_id, 'source_id': saved['id'], 'source_hash': detail['source_hash'],
                    'mcap_sha256': source['mcap_sha256'], 'provenance': provenance})
                write_json(path, receipt)
            actual = {pack['id']: pack for pack in call('GET', '/log-sim/packs', params={'project_id': project_id})['items']}
            if set(actual) != set(PACK_IDS) | {'input-errors'} or actual['input-errors']['status'] != 'input_example':
                raise ValueError('The six expected beta task packs are not available')
            for saved in entry['packs']:
                pack = actual[saved['id']]
                if pack['status'] != 'ready' or not pack.get('binding') or any(pack['binding'][key] != saved[key] for key in ('source_id', 'source_hash')):
                    raise ValueError(f"Pack {saved['id']} is not bound to the prepared source")
            entry.update(ready=True, verified_at=utcnow())
            write_json(path, receipt)
        receipt.update(ready=True, verified_at=utcnow())
        write_json(path, receipt)
        return receipt
    finally:
        if owns_client:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('--source-workspace', type=Path, required=True)
    parser.add_argument('--pack-jobs', type=Path, required=True, help='JSON mapping five pack IDs to retained execution IDs')
    parser.add_argument('--api-url', default='http://127.0.0.1:8071')
    parser.add_argument('--participants', type=int, choices=(3, 4, 5))
    parser.add_argument('--participant-label', action='append', help='Repeat once per participant; defaults to numbered labels')
    args = parser.parse_args(argv)
    participant_labels(args.participants, args.participant_label)
    if urlparse(args.api_url).hostname != '127.0.0.1':
        parser.error('This preparation helper is local-only')
    sources = retained_sources(args.source_workspace, json.loads(args.pack_jobs.read_text()))
    result = prepare(args.runtime, args.api_url, sources, participants=args.participants, labels=args.participant_label)
    entries = result['participants'] if args.participants else [result]
    print(json.dumps({'ready': result['ready'], 'projects': [entry['project_id'] for entry in entries],
        'participant_count': len(entries), 'packs_per_project': 6, 'executions_submitted': 0}))


if __name__ == '__main__':
    main()
