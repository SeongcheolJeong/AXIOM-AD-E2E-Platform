"""Case condition/review/coverage boundaries; fixtures are not scene truth."""
from copy import deepcopy
import hashlib

import pytest
from sqlalchemy import func, select

from ade2e import case_conditions as cc
from ade2e.case_models import CaseRow, CaseRevisionRow
from ade2e.job_models import JobRow
from test_detection_evaluations import context, evaluation_context, execute, submit, revise
from test_cases import setup_case, revision_body


def annotation(revision=1, **changes):
    return {'expected_revision': revision, 'taxonomy_id': cc.TAXONOMY_ID,
        'taxonomy_revision': 1, 'taxonomy_hash': cc.taxonomy()['manifest_hash'],
        'values': {'lighting': 'night', 'target': 'pedestrian', 'target_size': 'small'},
        'review_status': 'reviewed', 'author': 'Fixture author (declared)',
        'note': 'Authored condition declaration for contract tests, NOT inferred from fixture pixels.', **changes}


def save(client, case, key='conditions', **changes):
    response = client.post(f'/api/v1/cases/{case["id"]}/conditions', json=annotation(case['revision'], **changes),
        headers={'Idempotency-Key': key})
    assert response.status_code == 201, response.text
    return response.json()


def cov(client, case):
    response = client.get('/api/v1/case-condition-coverage', params={'recording_id': case['segment']['recording_id']})
    assert response.status_code == 200, response.text
    return response.json()


def test_declaration_is_new_revision_old_bytes_survive_retry_and_no_jobs(evaluation_context):
    client, app, original_body, case, _ = setup_case(evaluation_context)
    original = client.get(case['download_url']).content
    with app.state.db.session() as s: before = s.scalar(select(func.count()).select_from(JobRow))
    changed = save(client, case)
    assert changed['revision'] == 2 and changed['schema_version'] == 'axiom-investigation-case/2'
    assert changed['conditions']['taxonomy'] == cc.taxonomy()
    assert changed['conditions']['segment_hash'] == case['segment_hash']
    assert changed['conditions']['source'] == 'manual' and changed['conditions']['author_is_authenticated'] is False
    assert save(client, case) == changed
    assert client.get(case['download_url']).content == original
    assert client.post('/api/v1/cases', json=original_body, headers={'Idempotency-Key': 'create-case'}).json() == case
    assert client.post(f'/api/v1/cases/{case["id"]}/conditions', json=annotation(), headers={'Idempotency-Key': 'stale'}).status_code == 409
    assert client.post(f'/api/v1/cases/{case["id"]}/conditions', json=annotation(note='changed'), headers={'Idempotency-Key': 'conditions'}).status_code == 409
    with app.state.db.session() as s: assert s.scalar(select(func.count()).select_from(JobRow)) == before


def test_partial_draft_unknown_and_all_states_remain_in_denominator(evaluation_context):
    client, app, body, case, _ = setup_case(evaluation_context)
    second = client.post('/api/v1/cases', json=body, headers={'Idempotency-Key': 'second'}).json()
    third = client.post('/api/v1/cases', json=body, headers={'Idempotency-Key': 'third'}).json()
    initial = cov(client, case)
    assert initial['denominator']['unclassified_cases'] == 3
    save(client, case)
    save(client, second, key='draft', review_status='draft')
    save(client, third, key='partial', values={'lighting': 'night'})
    report = cov(client, case)
    assert report['denominator'] == {'total_cases': 3, 'classified_cases': 1, 'unclassified_cases': 2,
        'exact_source_selections': 1, 'repeated_selection_cases': 2, 'grid_cells': 36,
        'cells_with_cases': 1, 'cells_with_complete_linked_rows': 1}
    assert sum(c['cases'] for c in report['cells']) == 1
    assert next(x for x in report['axes'] if x['key'] == 'lighting')['counts']['night'] == 2
    latest = client.get(f'/api/v1/cases/{case["id"]}').json()
    client.post(f'/api/v1/cases/{case["id"]}/revisions', json=revision_body(latest, disposition_note='Declared resolution') | {'state': 'resolved'}, headers={'Idempotency-Key': 'resolve'})
    assert cov(client, case)['denominator'] == report['denominator']
    assert cov(client, case)['cases'][0]['diagnostics']['rule_version'] == cc.RULE_VERSION


@pytest.mark.parametrize('changes', [{'taxonomy_hash': '0'*64}, {'taxonomy_revision': 2},
    {'taxonomy_revision': True}, {'taxonomy_revision': '1'},
    {'values': {'lighting': 'sunny'}}, {'values': {'unknown_axis': 'night'}}, {'values': {'target': None}},
    {'author': ' '}, {'note': ' '}, {'review_status': 'approved'}, {'expected_revision': True},
    {'source': 'vision-model'}])
def test_invalid_declarations_are_rejected(evaluation_context, changes):
    client, _, _, case, _ = setup_case(evaluation_context)
    response = client.post(f'/api/v1/cases/{case["id"]}/conditions', json=annotation(**changes), headers={'Idempotency-Key': 'bad'})
    assert response.status_code == 422, response.text
    assert client.get(f'/api/v1/cases/{case["id"]}').json()['revision'] == 1


def test_search_filters_intersect_with_recording_and_review_not_page(evaluation_context):
    client, _, body, case, _ = setup_case(evaluation_context)
    draft = client.post('/api/v1/cases', json=body, headers={'Idempotency-Key': 'draft-case'}).json()
    save(client, case); save(client, draft, key='draft', review_status='draft')
    params = [('condition', 'lighting:night'), ('condition', 'target:pedestrian'), ('review', 'reviewed'), ('limit', 1)]
    result = client.get('/api/v1/cases', params=params).json()
    assert result['total_matches'] == 1 and result['items'][0]['id'] == case['id'] and result['next_offset'] is None
    assert client.get('/api/v1/cases', params={'q': 'unrelated'}).json()['items'] == []
    assert client.get('/api/v1/cases', params=[('condition', 'lighting:night'), ('condition', 'lighting:daylight')]).status_code == 422
    assert client.get('/api/v1/cases', params={'condition': 'bad'}).status_code == 422


def test_export_pins_census_and_refuses_stale_after_any_included_revision(evaluation_context):
    client, _, _, case, _ = setup_case(evaluation_context)
    report = cov(client, case)
    raw = client.get(report['download_url']).content
    assert hashlib.sha256(raw).hexdigest() == report['manifest_hash'] and len(raw) == report['manifest_size_bytes']
    save(client, case)
    assert client.get(report['download_url']).status_code == 409
    newest = cov(client, case)
    assert newest['manifest_hash'] != report['manifest_hash']


def test_unreviewed_source_is_not_automatically_a_false_negative(evaluation_context):
    (client, app, *_), _, _, _, body = evaluation_context
    data = revise(evaluation_context, lambda d: d['images'][0].update(review_status='unreviewed'))
    result = execute(evaluation_context, submit(evaluation_context, body=body.model_copy(update={'dataset_revision': data['revision']})))
    case = client.post('/api/v1/cases', json={'title': 'Missing labels', 'note': 'Unreviewed source',
        'source': {'kind': 'evaluation', 'evaluation_id': result.id, 'image_id': result.report.rows[0].image_id}}, headers={'Idempotency-Key': 'unreviewed'}).json()
    save(client, case)
    report = cov(client, case)
    assert report['evaluation_rows']['unusable'] == 1
    assert report['cases'][0]['diagnostics']['tags'] == ['incomplete_evidence']
    assert report['denominator']['cells_with_complete_linked_rows'] == 0


def test_models_cannot_combine_rows_into_fake_complete_coverage():
    def link(evaluation, ordinal, fn=0):
        return {'evaluation_status': 'completed', 'evaluation_id': evaluation, 'row_status': 'evaluated',
            'diagnostics': {'tp': 1-fn, 'fp': 0, 'fn': fn}, 'input': {'ordinal': ordinal}, 'image_id': str(ordinal), 'manifest_hash': 'a'*64}
    case = {'segment': {'samples': [{'ordinal': 1}, {'ordinal': 2}]}, 'evaluations': [link('A', 1), link('B', 2, 1)]}
    result = cc.evidence_summary(case)
    assert result['state'] == 'partial' and result['complete_evaluation_ids'] == []
    assert result['tags'] == ['has_false_negative', 'incomplete_evidence']
    case['evaluations'].append(link('A', 2))
    result = cc.evidence_summary(case)
    assert result['complete_evaluation_ids'] == ['A'] and 'has_false_negative' in result['tags']


def test_census_bounds_and_corruption_fail_instead_of_silent_omission(evaluation_context, monkeypatch):
    client, app, _, case, _ = setup_case(evaluation_context)
    monkeypatch.setattr(cc, 'MAX_CENSUS_CASES', 0)
    assert client.get('/api/v1/case-condition-coverage').status_code == 413
    monkeypatch.setattr(cc, 'MAX_CENSUS_CASES', 1000)
    with app.state.db.session.begin() as s:
        s.get(CaseRevisionRow, (case['id'], 1)).manifest += b' '
    assert client.get('/api/v1/case-condition-coverage').status_code == 409


def test_unknown_recording_scope_is_not_an_empty_coverage_report(client):
    assert client.get('/api/v1/case-condition-coverage?recording_id=rec_missing').status_code == 404


def test_condition_mutation_preserving_outer_hash_still_fails_contract(evaluation_context):
    from ade2e.cases import _seal, _sealed
    client, app, _, case, _ = setup_case(evaluation_context)
    tagged = save(client, case)
    with app.state.db.session.begin() as session:
        row = session.get(CaseRevisionRow, (case['id'], tagged['revision']))
        value = _sealed(row); value['conditions']['taxonomy']['axes'][0]['values'][0]['name'] = 'Tampered meaning'
        for key, content in _seal(value).items(): setattr(row, key, content)
    assert client.get(f'/api/v1/cases/{case["id"]}').status_code == 409


def test_removal_and_old_read_preserve_taxonomy_without_migration(evaluation_context):
    client, app, _, case, _ = setup_case(evaluation_context)
    tagged = save(client, case)
    cleared = save(client, tagged, key='clear', values={}, note='Remove uncertain declarations')
    assert cov(client, case)['denominator']['unclassified_cases'] == 1
    assert client.get(f'/api/v1/cases/{case["id"]}?revision=2').json() == tagged
    assert cleared['segment'] == case['segment']


def test_auth_and_agent_contract(evaluation_context):
    client, app, _, case, _ = setup_case(evaluation_context)
    tools = client.get('/api/v1/agent/tools').json()['tools']
    assert next(x for x in tools if x['name'] == 'classify_investigation_case')['required_headers'] == ['Idempotency-Key']
    app.state.settings.api_token = 'condition-test-only-token'
    assert client.get('/api/v1/case-condition-coverage').status_code == 401
    assert client.get('/api/v1/case-condition-taxonomy').status_code == 401
    assert client.post(f'/api/v1/cases/{case["id"]}/conditions', json=annotation(), headers={'Idempotency-Key': 'unauth'}).status_code == 401
