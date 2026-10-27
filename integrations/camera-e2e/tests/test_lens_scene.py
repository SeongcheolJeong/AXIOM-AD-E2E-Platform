"""Contract fixtures only; actual native acceptance is recorded separately."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
from ade2e.experiment_schemas import ExperimentCreate
from axiom_camera_e2e.lens_scene import LensSceneHandler, SLOTS, SPEC_SCHEMA, _hash, _accepted_job, _plan_checked, _quality_bindings_checked, _download_route
from axiom_camera_e2e.retained import canonical

CONTRACT = {'scope':'central_slanted_edge_model_diagnostic_only','comparison_map_wavelength_nm':550.0,
    'coordinate_model':'strict_raytrace_radial_geometry_inverse_piecewise_linear',
    'coordinate_convention':'row_positive_down_col_positive_right_degrees','source_wavelength_nm':[450.0,550.0,650.0],
    'physical_validation':'not_established','product_ranking_allowed':False,'pixel_integrated_effective_area_matched':False,
    'perception_label_correspondence':'not_established'}

class Fixture(LensSceneHandler):
    def __init__(self):
        self.url='http://127.0.0.1:8773';self.token='';self.calls=[];self.changed_slot=None
    def sources(self):return {'fixed_isp_profile':{'fixture':'fixed ISP'},'lenses':[],'sensors':[]}
    def _request(self,path,body=None,**kwargs):
        self.calls.append((path,deepcopy(body)))
        if path.endswith('/plan'):
            s=body['specification'];environment={'fixture':'source v1'}
            if s['name'].endswith(self.changed_slot or '__never__'):environment={'fixture':'source v2'}
            f={'specification':s,'environment':environment,'job_kind':'lens_sensor_raw','source_identities':
               {'lens':{'revision':s['lens_revision_id']},'sensor':s['sensor_revision']}}
            return {'schema':'camera_lens_sensor_raw_plan_v1','frozen_request':deepcopy(f),'plan_hash':_hash(f),'output_contract':deepcopy(CONTRACT)}
        raise AssertionError('A drifted preview must never dispatch native work')

def spec(**changes):
    data={'schema_version':SPEC_SCHEMA,'lenses':{'L1':'lens1','L2':'lens2'},'sensors':{'S1':{'identifier':'s1','run_id':'run1'},'S2':{'identifier':'s2','run_id':'run2'}},
          'scene':{'scene_kind':'slanted_edge','sensor_size':[32,32],'scene_samples_row_col':[256,256],'scene_horizontal_fov_deg':.12,'luminance_cd_m2':1000.,'exposure_s':.004,'noise_flag':0},
          'profile':{'fixture':'fixed ISP'},'acknowledge_simulation_limits':True}
    return ExperimentCreate(name='Authored test',kind='sensor_capture',adapter_id='camera-e2e-lens-sensor-scene',comparison_kind='same_scene_sensor_ab',input=data,**changes)

def test_all_four_sources_frozen_before_dispatch_and_late_drift_blocks_every_child(tmp_path):
    h=Fixture();snapshot=h.prepare(spec());assert tuple(snapshot['raw_plans'])==SLOTS
    assert all(p['frozen_request']['specification']['seed']==42 for p in snapshot['raw_plans'].values())
    h.calls.clear();h.changed_slot='L2S2'
    with pytest.raises(ValueError,match='no matrix dispatched'):h.execute(snapshot,tmp_path,lambda:False)
    assert len(h.calls)==4 and all(path.endswith('/plan') for path,_ in h.calls)
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('case',['project','comparison','model','same_sensor','same_lens','scene','profile','limits'])
def test_unsupported_boundaries_fail_before_native_plan(case):
    s=spec();h=Fixture()
    if case=='project':s.project_id='other'
    elif case=='comparison':s.comparison_kind='single'
    elif case=='model':s.model={'id':'unconnected'}
    elif case=='same_sensor':s.input['sensors']['S2']['run_id']='run1'
    elif case=='same_lens':s.input['lenses']['L2']='lens1'
    elif case=='scene':s.input['scene']['scene_kind']='road'
    elif case=='profile':s.input['profile']={'fixture':'different ISP'}
    else:s.input['acknowledge_simulation_limits']=False
    with pytest.raises(ValueError):h.prepare(s)
    assert h.calls==[]

@pytest.mark.parametrize('case',['digest','physical','ranking','footprint','labels','environment','accepted_hash'])
def test_native_child_and_scope_contracts_are_fail_closed(case):
    h=Fixture();p=h.prepare(spec())['raw_plans']['L1S1'];job={**deepcopy(p['frozen_request']),'id':'native_fixture','accepted_plan_hash':p['plan_hash']}
    if case=='digest':p['plan_hash']='0'*64
    elif case=='physical':p['output_contract']['physical_validation']='established'
    elif case=='ranking':p['output_contract']['product_ranking_allowed']=True
    elif case=='footprint':p['output_contract']['pixel_integrated_effective_area_matched']=True
    elif case=='labels':p['output_contract']['perception_label_correspondence']='ground_truth'
    elif case=='environment':job['environment']={'changed':True}
    else:job['accepted_plan_hash']='f'*64
    with pytest.raises(ValueError):_accepted_job(job,p,'raw')

def test_changed_origin_or_root_digest_rejects_before_native_http(tmp_path):
    h=Fixture();snapshot=h.prepare(spec());h.calls=[];snapshot['root_plan_hash']='0'*64
    with pytest.raises(ValueError,match='digest'):h.execute(snapshot,tmp_path,lambda:False)
    assert h.calls==[]
    snapshot=h.prepare(spec());h.calls=[];h.url='http://127.0.0.1:8774'
    with pytest.raises(ValueError,match='service changed'):h.execute(snapshot,tmp_path,lambda:False)
    assert h.calls==[]

def test_known_external_child_id_survives_rejected_submit_response(tmp_path):
    h=Fixture();snapshot=h.prepare(spec());request=h._request
    def changed(path,body=None,**kw):
        if path.endswith('/planned'):
            return {'id':'known_native_child','accepted_plan_hash':'f'*64,'status':'running'}
        return request(path,body,**kw)
    h._request=changed
    with pytest.raises(ValueError,match='accepted input'):h.execute(snapshot,tmp_path,lambda:False)
    receipt=json.loads((tmp_path/'camera-scene-external-dispatch.json').read_text())
    assert receipt['native_children']['L1S1']['id']=='known_native_child'
    assert receipt['native_children']['L1S1']['status']=='response_received_unverified'
    assert receipt['cleanup_status']=='unknown'

@pytest.mark.parametrize('change',['missing','duplicate','capture_result','raw_job','lens','sensor','blob'])
def test_derived_quality_preserves_exact_crossed_original_sources(change):
    plans=Fixture().prepare(spec())['raw_plans'];jobs={s:'job_'+s for s in SLOTS};exports={s:{'result':{'summary':{'slot':s}},'artifacts':[]} for s in SLOTS}
    entries=[{'slot':s,'source_job_id':jobs[s],'source_capture_result_sha256':_hash(exports[s]['result']),
        'source_binding':{'lens_identity':plans[s]['frozen_request']['source_identities']['lens'],
                          'saved_sensor_revision':plans[s]['frozen_request']['source_identities']['sensor']}} for s in SLOTS]
    _quality_bindings_checked(entries,jobs,exports,plans)
    if change=='missing':entries.pop()
    elif change=='duplicate':entries[-1]=deepcopy(entries[0])
    elif change=='capture_result':entries[0]['source_capture_result_sha256']='0'*64
    elif change=='raw_job':entries[0]['source_job_id']='other'
    elif change=='lens':entries[0]['source_binding']['lens_identity']={'revision':'different'}
    elif change=='sensor':entries[0]['source_binding']['saved_sensor_revision']={'run_id':'different'}
    else:entries[0]['source_artifact']={'id':'missing','name':'raw.npy','sha256':'a'*64,'bytes':32,'media_type':'application/octet-stream'}
    with pytest.raises(ValueError):_quality_bindings_checked(entries,jobs,exports,plans)

@pytest.mark.parametrize('route',['http://other/raw','/api/v1/artifacts/wrong','/api/v1/artifacts/blob?q=1','/api/v1/lens-sensor-quality-studies/other/diagnostic-artifacts/blob'])
def test_native_download_route_cannot_escape_its_pinned_artifact_and_job(route):
    artifact={'id':'blob','name':'native.json','sha256':'a'*64,'bytes':32,'media_type':'application/json','url':route}
    with pytest.raises(ValueError):_download_route(artifact,'known')

def test_declared_job_bound_diagnostic_route_is_used_without_weakening_sha():
    artifact={'id':'blob','name':'native.json','sha256':'a'*64,'bytes':32,'media_type':'application/json','url':'/api/v1/lens-sensor-quality-studies/known/diagnostic-artifacts/blob'}
    assert _download_route(artifact,'known')==artifact['url']
    with pytest.raises(ValueError):_download_route(artifact)
