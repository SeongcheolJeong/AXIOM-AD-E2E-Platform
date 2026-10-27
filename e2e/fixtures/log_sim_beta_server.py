from pathlib import Path
import secrets,threading,time
import uvicorn
from fastapi.staticfiles import StaticFiles
from ade2e.main import create_app
from ade2e.database import Settings,Database
from ade2e.planning_replay import PlanningReplayHandler
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.worker import run_once
from ade2e.adapters import Registry
import argparse
parser=argparse.ArgumentParser(description='Isolated Log Sim browser fixture; reference adapter only')
parser.add_argument('runtime',type=Path)
parser.add_argument('--port',type=int,default=8071)
args=parser.parse_args()
root=args.runtime.resolve();(root/'access').mkdir(parents=True,exist_ok=True)
key=root/'access/operator-token'
if not key.exists():key.write_text(secrets.token_urlsafe(32));key.chmod(0o600)
settings=Settings(data_dir=root/'workspace',api_token=key.read_text().strip(),team_mode=True,seed_examples=False)
db=Database(settings);db.initialize()
registry=ExperimentRegistry([PlanningReplayHandler(db)],load_plugins=False)
app=create_app(settings,experiment_registry=registry)
app.mount('/',StaticFiles(directory=Path.cwd()/'frontend/dist',html=True),name='beta-ui')
def work():
 while True:
  try:run_once(db,Registry(),'log-beta-reference-only',experiment_registry=registry)
  except Exception:
   import logging;logging.exception('Beta worker')
  time.sleep(.5)
threading.Thread(target=work,daemon=True).start()
uvicorn.run(app,host='127.0.0.1',port=args.port)
