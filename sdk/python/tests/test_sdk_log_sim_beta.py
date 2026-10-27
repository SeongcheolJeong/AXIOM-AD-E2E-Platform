import json
import httpx
from axiom_sdk import AxiomClient


def test_beta_shared_api_and_saved_comparison_pin(tmp_path):
    calls=[]
    def respond(r):
        calls.append(r)
        return httpx.Response(200, json={'items':[], 'ok':True})
    file=tmp_path/'input.mcap';file.write_bytes(b'mcap')
    ref={'id':'settings_a','revision':2,'sha256':'a'*64}
    with AxiomClient(transport=httpx.MockTransport(respond)) as c:
        c.upload_log_sim_mcap(file,project_id='beta')
        c.log_sim_uploads(project_id='beta')
        c.inspect_log_sim_input(project_id='beta',recording_id='rec_a',ego_channel_id=1,objects_channel_id=2,title='Interval',sample_count=12)
        c.register_log_sim_input(project_id='beta',preview_hash='a'*64,recording_id='rec_a')
        c.save_log_sim_comparison_settings(idempotency_key='settings',project_id='beta',name='A/B',candidate={'time_headway_s':4})
        c.log_sim_comparison_settings(project_id='beta')
        c.preview_log_sim_batch(['source_a'],project_id='beta',comparison_ref=ref)
        c.submit_log_sim_batch(['source_a'],project_id='beta',comparison_ref=ref,preview_hash='b'*64,idempotency_key='run')
        c.save_log_sim_review(idempotency_key='review',project_id='beta',case_id='case_a',source_id='source_a',note='Observation',expected_revision=0)
        c.log_sim_reviews('case_a',project_id='beta')
        c.export_log_sim_batch('batch_a',project_id='beta')
        c.export_log_sim_batch('batch_a',project_id='beta',format='csv')
        c.log_sim_packs(project_id='beta')
        c.bind_log_sim_pack('cut-in',project_id='beta',source_id='source_a',source_hash='a'*64,note='Pinned')
        c.log_sim_example(project_id='beta',invalid=True)
    assert calls[0].content==b'mcap' and calls[0].headers['Content-Type']=='application/octet-stream'
    assert json.loads(calls[6].content)['comparison_ref']==ref
    assert json.loads(calls[7].content)==json.loads(calls[6].content)|{'preview_hash':'b'*64}
    assert calls[7].headers['Idempotency-Key']=='run'
    assert calls[8].headers['Idempotency-Key']=='review'
    assert calls[9].url.params['case_id']=='case_a'
    assert calls[11].url.params['format']=='csv'
    assert calls[-1].url.params['invalid']=='true'
