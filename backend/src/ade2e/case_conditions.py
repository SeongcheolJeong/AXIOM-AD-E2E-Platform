"""I2: declared scene conditions and pinned-evidence coverage, not ODD safety.

The initial taxonomy is a versioned, built-in contract. Manual declarations are
stored in NEW case revisions; old case/1 bytes and request hashes stay unchanged.
Deterministic diagnostic tags use linked evidence only, never infer scene labels.
"""
from copy import deepcopy
from itertools import product
import json
from typing import Literal
from urllib.parse import urlencode

from fastapi import Header, HTTPException, Query, Response
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select

from . import jobs
from .base_schemas import StrictModel
from .case_models import CaseRevisionRow, CaseRow, SegmentRow
from .service import canonical_bytes, digest, utcnow

TAXONOMY_ID = 'investigation-conditions'
TAXONOMY_VERSION = 1
MAX_CENSUS_CASES = 1000
MAX_CENSUS_BYTES = 32 * 1024 * 1024
RULE_VERSION = 'pinned-row-diagnostics/1'
TAGS = ('has_false_negative', 'has_false_positive', 'incomplete_evidence', 'no_linked_evaluation')
ReviewState = Literal['all', 'reviewed', 'draft', 'unclassified']
DiagnosticTag = Literal['has_false_negative', 'has_false_positive', 'incomplete_evidence', 'no_linked_evaluation']

# These are declared investigation conditions, not measured environment truth.
_TAXONOMY_BYTES = canonical_bytes({
    'id': TAXONOMY_ID, 'revision': TAXONOMY_VERSION, 'schema_version': 'axiom-condition-taxonomy/1',
    'axes': [
        {'key': 'lighting', 'name': 'Lighting', 'definition': 'Lighting for the selected source; mixed or unverified conditions remain unclassified.',
         'values': [{'id': 'daylight', 'name': 'Daylight'}, {'id': 'night', 'name': 'Night'}, {'id': 'low_light', 'name': 'Other low light'}]},
        {'key': 'target', 'name': 'Primary target', 'definition': 'One declared investigation target, not a census of every object in the segment.',
         'values': [{'id': 'pedestrian', 'name': 'Pedestrian'}, {'id': 'vehicle', 'name': 'Vehicle'},
                    {'id': 'cyclist', 'name': 'Cyclist'}, {'id': 'other', 'name': 'Other'}]},
        {'key': 'target_size', 'name': 'Target image size',
         'definition': 'Declared target bbox area in ORIGINAL image pixels: small <1024; medium 1024 to <9216; large >=9216. Cite image/annotation in the note. Not range or physical size; omit for non-image/ambiguous targets.',
         'values': [{'id': 'small', 'name': 'Small'}, {'id': 'medium', 'name': 'Medium'}, {'id': 'large', 'name': 'Large'}]},
    ],
    'grid_policy': 'Cartesian review grid of 36 declared combinations. No ODD validity, independence, safety probability or required test allocation is implied.',
    'unknown_policy': 'Missing values and draft declarations remain outside the classified grid; never map them to daylight/other.',
    'editable_in_this_version': False,
})


def taxonomy():
    value = json.loads(_TAXONOMY_BYTES)
    return {**value, 'manifest_hash': digest(value)}


def axis_options():
    return {axis['key']: {v['id'] for v in axis['values']} for axis in taxonomy()['axes']}


def check_values(values):
    options = axis_options()
    if not isinstance(values, dict) or any(k not in options or v not in options[k] for k, v in values.items()):
        raise ValueError('Use only the declared taxonomy axes/values; leave unknown axes absent')
    return values


class ConditionsCreate(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    taxonomy_id: Literal['investigation-conditions']
    taxonomy_revision: Literal[1]
    taxonomy_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    values: dict[str, str] = Field(default_factory=dict, max_length=3)
    review_status: Literal['draft', 'reviewed'] = 'draft'
    author: str = Field(min_length=1, max_length=180)
    note: str = Field(min_length=1, max_length=4000)

    @field_validator('taxonomy_revision', mode='before')
    @classmethod
    def strict_taxonomy_revision(cls, value):
        if type(value) is not int:
            raise ValueError('Taxonomy revision must be an integer, not a bool or string')
        return value

    @field_validator('author', 'note')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('A declared author and source/review note are required')
        return value.strip()

    @model_validator(mode='after')
    def declared(self):
        check_values(self.values)
        if self.taxonomy_hash != taxonomy()['manifest_hash']:
            raise ValueError('Taxonomy changed or is unknown; reload the exact taxonomy')
        return self


def validate_annotation(case):
    annotation = case.get('conditions')
    if annotation is None:
        return
    try:
        if set(annotation) != {'schema_version', 'taxonomy', 'values', 'review_status', 'author', 'note',
                               'source', 'segment_hash', 'declared_at', 'author_is_authenticated'}:
            raise ValueError('Unknown condition annotation fields')
        if (case['schema_version'] not in ('axiom-investigation-case/2', 'axiom-investigation-case/3', 'axiom-investigation-case/4', 'axiom-investigation-case/5')
                or annotation['schema_version'] != 'axiom-case-conditions/1'
                or annotation['taxonomy'] != taxonomy() or annotation['source'] != 'manual'
                or annotation['segment_hash'] != case['segment_hash']
                or annotation['author_is_authenticated'] is not False):
            raise ValueError('Condition source/taxonomy identity mismatch')
        ConditionsCreate(expected_revision=1, taxonomy_id=TAXONOMY_ID, taxonomy_revision=1,
            taxonomy_hash=annotation['taxonomy']['manifest_hash'], **{k: annotation[k] for k in ('values', 'review_status', 'author', 'note')})
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(409, 'Case condition declaration failed its contract check') from error


def condition_summary(case):
    annotation = case.get('conditions')
    return {'values': annotation['values'] if annotation else {},
            'review_status': annotation['review_status'] if annotation and annotation['values'] else 'unclassified'}


def evidence_summary(case):
    """Rows are complete only within ONE evaluation, never a union of models."""
    expected = {row['ordinal'] for row in case['segment']['samples']}
    by_evaluation, tags, refs = {}, set(), []
    for link in case['evaluations']:
        valid = link['evaluation_status'] == 'completed' and link['row_status'] == 'evaluated' and link['diagnostics'] is not None
        if valid:
            by_evaluation.setdefault(link['evaluation_id'], set()).add(link['input']['ordinal'])
            row_tags = []
            for count, tag in (('fn', 'has_false_negative'), ('fp', 'has_false_positive')):
                if link['diagnostics'][count] > 0:
                    tags.add(tag); row_tags.append(tag)
            refs.append({'evaluation_id': link['evaluation_id'], 'image_id': link['image_id'],
                'manifest_hash': link['manifest_hash'], 'tags': row_tags})
    complete = sorted(key for key, ordinals in by_evaluation.items() if ordinals == expected)
    if not case['evaluations']:
        tags.add('no_linked_evaluation')
    if (not complete or any(link['evaluation_status'] != 'completed' or link['row_status'] != 'evaluated'
                           or link['diagnostics'] is None for link in case['evaluations'])):
        tags.add('incomplete_evidence')
    state = 'complete' if complete else 'partial' if by_evaluation else 'unusable' if case['evaluations'] else 'unlinked'
    return {'state': state, 'rule_version': RULE_VERSION, 'tags': sorted(tags), 'evidence': refs,
        'complete_evaluation_ids': complete, 'linked_rows': len(case['evaluations']),
        'source_samples': len(expected), 'best_single_evaluation_samples': max(map(len, by_evaluation.values()), default=0),
        'meaning': 'Linked usable rows for this pinned segment only, not whole-dataset coverage, pass/fail or independent scenarios.'}


def summary(case):
    return {**{k: case[k] for k in ('id', 'revision', 'title', 'state', 'assignee', 'created_at', 'manifest_hash')},
            'conditions': condition_summary(case), 'diagnostics': evidence_summary(case)}


def parse_filters(conditions):
    if len(conditions) > 3:
        raise HTTPException(422, 'At most one condition per taxonomy axis is supported')
    selected = {}
    try:
        for term in conditions:
            key, value = term.split(':')
            if key in selected:
                raise ValueError('Duplicate condition axis')
            selected[key] = value
        check_values(selected)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, 'Use unique axis:value conditions from the taxonomy') from error
    return selected


def census(session, query):
    from .cases import _get
    heads = list(session.scalars(query.limit(MAX_CENSUS_CASES + 1)))
    if len(heads) > MAX_CENSUS_CASES:
        raise HTTPException(413, 'Condition analysis supports at most 1000 cases; scope to one recording. No partial coverage is returned.')
    rows, size = [], 0
    for head in heads:
        value = _get(session, head.id)
        size += value['manifest_size_bytes']
        if size > MAX_CENSUS_BYTES:
            raise HTTPException(413, 'Condition analysis exceeds 32 MiB of case manifests; narrow the recording scope.')
        # Equal source sets are counted separately as cases, but reported as duplicates.
        source = value['segment']
        source_key = digest({'recording': source['recording_sha256'], 'channel': source['channel_id'],
            'samples': sorted((s['ordinal'], s['sha256']) for s in source['samples'])})
        rows.append({**summary(value), 'source_selection_hash': source_key,
            'recording_id': source['recording_id'], '_text': ' '.join((value['title'], value['note'], value['assignee'])).casefold()})
    return rows


def filter_rows(rows, *, q='', conditions=(), review='all', diagnostic=None):
    selected = parse_filters(conditions)
    return [row for row in rows if (not q or q.strip().casefold() in row['_text'])
        and all(row['conditions']['values'].get(k) == v for k, v in selected.items())
        and (review == 'all' or row['conditions']['review_status'] == review)
        and (not diagnostic or diagnostic in row['diagnostics']['tags'])]


def coverage(session, recording_id=None):
    query = select(CaseRow).join(SegmentRow, CaseRow.segment_id == SegmentRow.id).order_by(CaseRow.id)
    if recording_id:
        from .recordings import _get_recording
        _get_recording(session, recording_id)
        query = query.where(SegmentRow.recording_id == recording_id)
    rows = census(session, query)
    axes = taxonomy()['axes']
    grid = []
    classified = []
    for row in rows:
        c = row['conditions']
        if c['review_status'] == 'reviewed' and len(c['values']) == len(axes):
            classified.append(row)
    for values in product(*(axis['values'] for axis in axes)):
        conditions = {axis['key']: v['id'] for axis, v in zip(axes, values)}
        matches = [r for r in classified if r['conditions']['values'] == conditions]
        grid.append({'conditions': conditions, 'case_ids': [r['id'] for r in matches], 'cases': len(matches),
            'with_complete_linked_rows': sum(r['diagnostics']['state'] == 'complete' for r in matches),
            'with_observed_errors': sum(bool(set(r['diagnostics']['tags']) & {'has_false_negative', 'has_false_positive'}) for r in matches)})
    axis_counts = []
    for axis in axes:
        counts = {v['id']: 0 for v in axis['values']}; counts['unclassified'] = 0
        for row in rows:
            c = row['conditions']; key = c['values'].get(axis['key']) if c['review_status'] == 'reviewed' else None
            counts[key or 'unclassified'] += 1
        axis_counts.append({'key': axis['key'], 'counts': counts})
    unique = len({r['source_selection_hash'] for r in rows})
    for row in rows:
        del row['_text']
    report = {'schema_version': 'axiom-case-condition-coverage/1', 'taxonomy': taxonomy(),
        'scope': {'recording_id': recording_id, 'case_revision_policy': 'current', 'case_states': 'all_including_resolved_and_dismissed',
                  'unit': 'investigation_case', 'source_bytes_revalidated': False},
        'denominator': {'total_cases': len(rows), 'classified_cases': len(classified),
            'unclassified_cases': len(rows) - len(classified), 'exact_source_selections': unique,
            'repeated_selection_cases': len(rows) - unique, 'grid_cells': len(grid),
            'cells_with_cases': sum(c['cases'] > 0 for c in grid),
            'cells_with_complete_linked_rows': sum(c['with_complete_linked_rows'] > 0 for c in grid)},
        'evaluation_rows': {state: sum(r['diagnostics']['state'] == state for r in rows) for state in ('complete', 'partial', 'unusable', 'unlinked')},
        'axes': axis_counts, 'cells': grid, 'cases': rows,
        'limitations': ['Scene conditions are manually declared; reviewed means a declared review, not authenticated approval or measured truth.',
            'This is case-inventory and linked-row coverage, not ODD, safety, full-dataset/model coverage, execution coverage or a test pass.',
            'One complete linked evaluation does not erase missing evidence or errors in another model; error tags consider all pinned links.',
            'Exact-source duplicates are disclosed. Overlapping segments are not deduplicated and are not independent scenes.',
            'Empty cells are inventory gaps, not proof that a required/physically valid scenario was missed. No tests are scheduled.',
            'Source byte health is not revalidated by this metadata census; use case source samples for byte validation.']}
    raw = canonical_bytes(report)
    if len(raw) > MAX_CENSUS_BYTES:
        raise HTTPException(413, 'Coverage export exceeds 32 MiB; narrow the recording scope')
    return report, raw


def classify(db, case_id, body, key):
    from .cases import _get, _publish
    request_hash = digest({'operation': 'case-conditions/1', 'case_id': case_id, **body.model_dump(mode='json')})
    storage_key = 'conditions:' + key
    with jobs._write(db) as session:
        previous_request = session.scalar(select(CaseRevisionRow).where(CaseRevisionRow.request_key == storage_key))
        if previous_request:
            if previous_request.request_hash != request_hash or previous_request.case_id != case_id:
                raise HTTPException(409, 'Condition Idempotency-Key already identifies another request')
            return _get(session, case_id, previous_request.revision)
        prior = _get(session, case_id)
        if prior['revision'] != body.expected_revision:
            raise HTTPException(409, 'Case revision changed; reload before editing conditions')
        value = deepcopy({k: v for k, v in prior.items() if k not in ('manifest_hash', 'manifest_size_bytes', 'download_url')})
        value.update(schema_version='axiom-investigation-case/5' if prior.get('stack_replays') else 'axiom-investigation-case/4' if prior.get('model_replays') else 'axiom-investigation-case/3' if prior.get('retests') else 'axiom-investigation-case/2', revision=prior['revision'] + 1,
            created_at=utcnow(), previous_manifest_hash=prior['manifest_hash'],
            conditions={'schema_version': 'axiom-case-conditions/1', 'taxonomy': taxonomy(), 'values': body.values,
                'source': 'manual', 'review_status': body.review_status, 'author': body.author, 'note': body.note,
                'segment_hash': prior['segment_hash'], 'declared_at': utcnow(), 'author_is_authenticated': False})
        return _publish(session, session.get(CaseRow, case_id), value, storage_key, request_hash)


def register_condition_routes(api, db):
    @api.get('/case-condition-taxonomy', tags=['investigation conditions'])
    def get_taxonomy():
        return taxonomy()

    @api.post('/cases/{case_id}/conditions', status_code=201, tags=['investigation conditions'])
    def post_conditions(case_id: str, body: ConditionsCreate, idempotency_key: str = Header(min_length=1, max_length=180)):
        return classify(db, case_id, body, idempotency_key)

    @api.get('/case-condition-coverage', tags=['investigation conditions'])
    def get_coverage(recording_id: str | None = Query(None, pattern=r'^[A-Za-z0-9_-]{1,100}$')):
        with db.read_session() as session:
            report, raw = coverage(session, recording_id)
            sha = digest(report)
            query = urlencode({'expected_hash': sha, **({'recording_id': recording_id} if recording_id else {})})
            return {**report, 'manifest_hash': sha, 'manifest_size_bytes': len(raw),
                'download_url': '/api/v1/case-condition-coverage/export?' + query}

    @api.get('/case-condition-coverage/export', tags=['investigation conditions'])
    def export_coverage(expected_hash: str = Query(..., pattern=r'^[a-f0-9]{64}$'),
                        recording_id: str | None = Query(None, pattern=r'^[A-Za-z0-9_-]{1,100}$')):
        with db.read_session() as session:
            report, raw = coverage(session, recording_id)
            if digest(report) != expected_hash:
                raise HTTPException(409, 'Case inventory changed; refresh coverage before export')
            return Response(raw, media_type='application/json', headers={'X-Content-Type-Options': 'nosniff',
                'Content-Disposition': f'attachment; filename="case-coverage-{expected_hash[:12]}.json"'})
