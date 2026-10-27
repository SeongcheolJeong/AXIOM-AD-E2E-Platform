"""Run: python examples/ecu_lab_workflow.py --url http://127.0.0.1:8099 --preset normal
Requires the API and worker with `pip install -e integrations/fpga_lab`.
"""
import argparse
import json
import time
import uuid
from axiom_sdk import AxiomClient


def reference(value):
    return {k:value[k] for k in ('id','revision','sha256')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8099')
    parser.add_argument('--token',default='')
    parser.add_argument('--project',default='default')
    parser.add_argument('--preset',default='normal')
    args = parser.parse_args()
    client = AxiomClient(args.url, token=args.token)
    key = 'ecu-example-'+uuid.uuid4().hex
    template = next(p for p in client.ecu_lab_templates(project_id=args.project)['items'] if p['id']==args.preset)
    profile = client.save_ecu_lab_profile(name=template['name'],config=template['profile']['config'],
        project_id=args.project,idempotency_key=key+'-profile')
    channels = []
    for channel in template['channels']:
        design = client.save_fpga_lab_design(name=f"CH{channel['channel']}",config=channel['config'],
            seed=template['seed'],project_id=args.project,idempotency_key=key+f"-ch{channel['channel']}")
        channels.append({k:v for k,v in channel.items() if k!='config'} | {'design_ref':reference(design)})
    test = client.save_ecu_lab_test(name=template['name'],profile_ref=reference(profile),channels=channels,
        commands=template['commands'],faults=template['faults'],seed=template['seed'],project_id=args.project,
        idempotency_key=key+'-test')
    job = client.execute_ecu_lab_test(test,idempotency_key=key+'-run')
    for _ in range(120):
        run = client.experiment_execution(job['id'])
        if run['status'] in {'completed','failed','cancelled'}:
            print(json.dumps(run,ensure_ascii=False,indent=2))
            return
        time.sleep(1)
    raise TimeoutError(f"Execution {job['id']} still pending; resume with experiment_execution")

if __name__ == '__main__':
    main()
