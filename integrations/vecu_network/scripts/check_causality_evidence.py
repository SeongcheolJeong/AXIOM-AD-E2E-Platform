"""Read existing API/SDK reports and independently verify source identity/times.

This does not submit a simulation. Execution IDs are retained acceptance inputs.
"""
from pathlib import Path
import hashlib,json
from axiom_sdk import AxiomClient
from ade2e.service import digest

PROJECT='project_9f48a965d4194be0b18859e334e007bb'
CASES={
    'udp_delay':('job_e91f328d00674ea1a7979c708f613751',[3000,3250,3250,3251,3300,3350,3400]),
    'restbus_drop':('job_aa0406a441f44decb5b6a552a310f473',[3000,3091,3100,3101,3150,3200,3200]),
    'combined':('job_29945db7947e4c428309a9e16619e34f',[3000,3091,3100,3101,3150,3200,3200]),
}
root=Path.cwd();out=root/'docs/validation/vecu-network-causality-20261017';out.mkdir(exist_ok=True)
reports={}
with AxiomClient('http://127.0.0.1:8230') as sdk:
    for kind,(identity,expected) in CASES.items():
        result=sdk.vecu_network_execution(identity,project_id=PROJECT)
        raw=sdk.export_vecu_network_execution(identity,project_id=PROJECT)
        report=sdk.vecu_network_causality(identity,project_id=PROJECT)
        assert hashlib.sha256(raw).hexdigest()==result['manifest_hash']==report['manifest_sha256']
        assert report['sha256']==digest({k:v for k,v in report.items() if k!='sha256'})
        artifact=next(a for a in result['artifacts'] if a['name']=='vecu-trace.json')
        source=root/'e2e/.runtime/vecu-network-20261017/experiments/artifacts'/artifact['id']
        assert hashlib.sha256(source.read_bytes()).hexdigest()==artifact['sha256']==report['source_artifact']['sha256']
        trace=json.loads(source.read_bytes());case=report['cases'][0]
        assert [case['stages'][key]['time_ms'] for key in ('injection','detection','can_tx','can_rx','application','vehicle','slowdown')]==expected
        application=trace['sides']['faulted'][case['stages']['application']['step']]
        assert application['reply']['control']==application['next_ego']['control']
        assert application['reply']['control']['brake']==1.
        if kind=='combined':assert report['cases'][1]['status']=='unsupported'
        reports[kind]=report
        (out/(kind+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    assert reports['udp_delay']['comparison_identity_sha256']==reports['restbus_drop']['comparison_identity_sha256']
    listing=sdk.vecu_network_executions(project_id=PROJECT)
    assert all('faults' in item for item in listing['items'])
(out/'sdk-checks.json').write_text(json.dumps(dict(status='passed',classification='existing_actual_carla_evidence_readback',new_native_execution=False,source_reports={k:r['sha256'] for k,r in reports.items()},checks=['SDK/API same causality contract','sealed original manifest SHA','original control trace SHA','exact detection/CAN/application/vehicle times','CARLA applied control readback','reset injection is not detector evidence','matching comparison conditions','history includes frozen fault schedules']),indent=2)+'\n')
print(json.dumps({kind:report['cases'][0]['latency_ms'] for kind,report in reports.items()},indent=2))
