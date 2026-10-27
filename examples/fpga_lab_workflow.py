"""Run against an API + worker with the optional SimPy adapter installed."""
import argparse
import json
import time
import uuid
from axiom_sdk import AxiomClient

parser = argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8000')
parser.add_argument('--token')
parser.add_argument('--project',default='default')
args = parser.parse_args()
with AxiomClient(args.url, token=args.token) as client:
    config = client.fpga_lab_templates(project_id=args.project)['items'][0]['config']
    design = client.save_fpga_lab_design(name='RAW12 normal timing', config=config, project_id=args.project,
                                         idempotency_key=str(uuid.uuid4()))
    run = client.execute_fpga_lab_design(design, idempotency_key=str(uuid.uuid4()))
    deadline = time.monotonic() + 120
    while run['status'] not in ('completed','failed','cancelled','interrupted'):
        if time.monotonic() > deadline:
            raise TimeoutError(f"Worker did not complete {run['id']}; inspect existing execution, do not resubmit")
        time.sleep(.5)
        run = client.experiment_execution(run['id'])
    print(json.dumps(client.export_fpga_lab_execution(run['id']),ensure_ascii=False,indent=2))
