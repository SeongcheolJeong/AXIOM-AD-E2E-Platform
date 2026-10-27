"""Causal contract checks; authored inputs do not establish native VAD execution."""
import copy
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from ade2e.driving_contract import SOURCE_COMMIT, content_hash, validate_input, validate_result

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'integrations/vad/src'))
from axiom_vad.nuscenes_input import export_input, verify_files, file_hash


@pytest.fixture
def original(tmp_path):
    root=tmp_path/'nuscenes';(root/'v1.0-mini').mkdir(parents=True);(root/'samples').mkdir();(root/'can_bus').mkdir()
    pose={'token':'pose','translation':[1,2,3],'rotation':[1,0,0,0]}
    calib={'token':'lidar-cal','sensor_token':'lidar-sensor','translation':[0,0,0],'rotation':[1,0,0,0]}
    camera_cal={**calib,'token':'camera-cal','camera_intrinsic':[[10,0,8],[0,10,8],[0,0,1]]}
    from ade2e.driving_contract import CAMERAS
    samples=[];data=[];can=[]
    for i in range(3):
        timestamp=1000000+i*500000
        channels={name:f'{name}-{i}' for name in CAMERAS};channels['LIDAR_TOP']=f'lidar-{i}'
        samples.append({'token':f'sample-{i}','scene_token':'scene','prev':f'sample-{i-1}' if i else '',
            'next':f'sample-{i+1}' if i<2 else ''})
        for name in CAMERAS:
            filename=f'samples/{name}-{i}.png';Image.new('RGB',(16,16),(i*40,10,20)).save(root/filename)
            data.append({'token':channels[name],'sample_token':f'sample-{i}','timestamp':timestamp,'is_key_frame':True,
                'filename':filename,'width':16,'height':16,'ego_pose_token':'pose','calibrated_sensor_token':name+'-cal'})
        data.append({'token':f'lidar-{i}','sample_token':f'sample-{i}','is_key_frame':True,'timestamp':timestamp,'ego_pose_token':'pose','calibrated_sensor_token':'lidar-cal'})
        can.append({'utime':timestamp-1000,'accel':[0,0,0],'rotation_rate':[0,0,0],'vel':[1,0,0]})
    tables={'scene':[{'token':'scene','name':'scene-authored'}],'sample':samples,'sample_data':data,
        'calibrated_sensor':[calib,*[{**camera_cal,'token':name+'-cal','sensor_token':name+'-sensor'} for name in CAMERAS]],'ego_pose':[pose],
        'sensor':[{'token':'lidar-sensor','channel':'LIDAR_TOP','modality':'lidar'},* [{'token':name+'-sensor','channel':name,'modality':'camera'} for name in CAMERAS]]}
    for name,rows in tables.items():(root/'v1.0-mini'/f'{name}.json').write_text(json.dumps(rows))
    (root/'can_bus/scene-authored_pose.json').write_text(json.dumps(can))
    return root


@pytest.fixture
def driving_input(original):
    return export_input(original,'scene','sample-2',rights_note='Authored fixture; no actual nuScenes pixels or performance')


def receipt(value):
    return {'schema_version':'driving-result/1','input':value,'input_hash':content_hash(value),
        'model':{'source_commit':SOURCE_COMMIT,'source_files_hash':'a'*64,'adapter_source_hash':'e'*64,'checkpoint_sha256':'b'*64,
            'config_sha256':'c'*64,'resolved_config_hash':'d'*64,'adapter_version':'axiom-vad-native/1',
            'profile':value['profile'],'weights_rights':'Authored receipt, not native execution'},
        'execution':{'status':'completed','engine':'native_torch','device':'Authored fixture, not GPU execution',
            'library_versions':{'fixture':'1'},'seed':42,'elapsed_ms':0,'peak_cuda_bytes':0,'ground_truth_in_model_input':False},
        'outputs':[{'sample_token':f['sample_token'],'timestamp_us':f['timestamp_us'],'coordinate_frame':'current_lidar_xy_m',
            'step_seconds':.5,'modes':{name:[[i*factor,i] for i in range(1,7)] for name,factor in [('right',.2),('left',-.2),('straight',0)]}}
            for f in value['frames']], 'limitations':['Authored display fixture; no learned/native execution claim.']}


def test_real_file_hash_and_calibration_preflight(original,driving_input,client):
    verify_files(original,driving_input)
    response=client.post('/api/v1/driving-policies/preflight',json=driving_input)
    assert response.status_code==200,response.text
    check=response.json()
    assert check['frames']==3 and check['camera_observations']==18
    assert check['structural_status']=='ready' and check['native_execution_status']=='not_checked'
    assert check['file_identity_status']=='requires_native_file_check'
    assert client.get('/api/v1/driving-policies/capabilities').json()['native_execution_status']=='not_configured'


@pytest.mark.parametrize('change',['future_camera','future_can','missing_camera','repeated_sample','wrong_order','discontinuous','unknown_gt','nan','shape'])
def test_invalid_or_future_input_cannot_be_ready(driving_input,change,client):
    value=copy.deepcopy(driving_input);frame=value['frames'][1]
    if change=='future_camera':frame['cameras'][0]['timestamp_us']=frame['observed_until_us']+1
    elif change=='future_can':frame['can_bus_source']['timestamp_us']=frame['timestamp_us']+1
    elif change=='missing_camera':frame['cameras'].pop()
    elif change=='repeated_sample':frame['sample_token']=value['frames'][0]['sample_token']
    elif change=='wrong_order':frame['cameras'].reverse()
    elif change=='discontinuous':frame['previous_sample_token']='unrelated'
    elif change=='unknown_gt':frame['ego_fut_trajs']=[[1,2]]*6
    elif change=='nan':frame['can_bus'][7]=float('nan')
    else:frame['cameras'][0]['lidar2img']=[[1,0]]
    with pytest.raises(ValueError):validate_input(value)
    if change!='nan': assert client.post('/api/v1/driving-policies/preflight',json=value).status_code==422


@pytest.mark.parametrize('kind',['pixels','calibration','projection','can','missing','history'])
def test_native_file_checks_refuse_changed_or_fabricated_inputs(original,driving_input,kind):
    value=copy.deepcopy(driving_input)
    if kind=='pixels':(original/value['frames'][0]['cameras'][0]['path']).write_bytes(b'changed')
    elif kind=='calibration':
        p=original/'v1.0-mini/calibrated_sensor.json';rows=json.loads(p.read_text());rows[1]['translation'][0]=1;p.write_text(json.dumps(rows))
    elif kind=='projection':value['frames'][0]['cameras'][0]['lidar2img'][0][0]+=1
    elif kind=='can':value['frames'][0]['can_bus'][7]=99
    elif kind=='missing':(original/value['frames'][0]['can_bus_source']['path']).unlink()
    else:value['frames'][0]['sample_token']='invented-padding'
    with pytest.raises((ValueError,KeyError)):verify_files(original,value)


def test_no_can_or_real_history_is_not_zero_padded(original):
    with pytest.raises(ValueError,match='history'):export_input(original,'scene','sample-0',rights_note='fixture')
    p=original/'can_bus/scene-authored_pose.json';rows=json.loads(p.read_text());rows[0]['utime']=1000001;p.write_text(json.dumps(rows))
    with pytest.raises(ValueError,match='causal CAN'):export_input(original,'scene','sample-2',rights_note='fixture')


def test_receipt_inspection_preserves_all_modes_and_declares_unverified_origin(driving_input,client):
    result=receipt(driving_input)
    validate_result(result)
    response=client.post('/api/v1/driving-policies/inspect-result',json=result)
    assert response.status_code==200,response.text
    assert response.json()['origin_verification']=='self_reported_receipt_not_independently_verified'
    assert response.json()['verification_verdict']=='not_evaluated'
    assert set(response.json()['outputs'][0]['modes'])=={'right','left','straight'}
    result['input']['route_command']='right'
    assert client.post('/api/v1/driving-policies/inspect-result',json=result).status_code==422


@pytest.mark.parametrize('kind',['missing_frame','wrong_time','short_path','gt_input','wrong_source'])
def test_invalid_execution_receipts_are_not_inspected(driving_input,client,kind):
    result=receipt(driving_input)
    if kind=='missing_frame':result['outputs'].pop()
    elif kind=='wrong_time':result['outputs'][0]['timestamp_us']+=1
    elif kind=='short_path':result['outputs'][0]['modes']['straight'].pop()
    elif kind=='gt_input':result['execution']['ground_truth_in_model_input']=True
    else:result['model']['source_commit']='unknown'
    assert client.post('/api/v1/driving-policies/inspect-result',json=result).status_code==422


def test_native_environment_is_fail_closed_without_learning_dependencies():
    from axiom_vad.native import inspect_environment
    import importlib.util
    if importlib.util.find_spec('torch'):pytest.skip('This boundary checks the core environment without Torch')
    assert inspect_environment()['status']=='unavailable'


def test_driving_checks_share_private_workspace_authentication(tmp_path,driving_input):
    from fastapi.testclient import TestClient
    from ade2e.adapters import Registry
    from ade2e.database import Settings
    from ade2e.main import create_app
    with TestClient(create_app(Settings(data_dir=tmp_path/'private',seed_examples=False,api_token='private-test-token'),Registry(load_plugins=False))) as client:
        assert client.get('/api/v1/driving-policies/capabilities').status_code==401
        assert client.post('/api/v1/driving-policies/preflight',json=driving_input).status_code==401
        assert client.post('/api/v1/driving-policies/inspect-result',json=receipt(driving_input)).status_code==401
        assert client.post('/api/v1/driving-policies/preflight',json=driving_input,headers={'Authorization':'Bearer private-test-token'}).status_code==200


def test_original_tables_are_joined_without_decorated_sample_data(original,driving_input):
    samples=json.loads((original/'v1.0-mini/sample.json').read_text())
    assert all('data' not in row for row in samples)
    assert driving_input['frames'][0]['cameras'][0]['sample_data_token']=='CAM_FRONT-0'
    p=original/'v1.0-mini/sensor.json';rows=json.loads(p.read_text());rows[1]['channel']='CAM_UNKNOWN';p.write_text(json.dumps(rows))
    with pytest.raises(ValueError,match='metadata changed'):verify_files(original,driving_input)


def test_duplicate_keyframe_channel_is_rejected(original):
    p=original/'v1.0-mini/sample_data.json';rows=json.loads(p.read_text());rows.append({**rows[0],'token':'duplicate-camera'});p.write_text(json.dumps(rows))
    with pytest.raises(ValueError,match='Duplicate original keyframe channel'):export_input(original,'scene','sample-2',rights_note='fixture')
