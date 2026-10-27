"""Optional Apollo timing and image adapter. The core never imports NumPy/OpenCV."""
import csv
import hashlib
import json
import shutil
import time
from pathlib import Path
import numpy as np
import cv2
from PIL import Image, ImageDraw
from ade2e.apollo_lab_schemas import ADAPTER,FIDELITY
from ade2e.experiment_runtime import ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile
from ade2e.service import canonical_bytes
from .engine import simulate
from .pixels import decode,synthetic,process

class ApolloHandler:
    def __init__(self,database=None):self.database=database
    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER,version='0.1.0',kinds=['apollo_camera'],status='available',fidelity=FIDELITY,cancellation_supported=True,description='SM8750 topology / SimPy timing / CPU reference pixels; not Qualcomm firmware or silicon')
    def verify_assets(self,snapshot):
        from ade2e.apollo_lab import asset_path
        for c in snapshot['channels']:
            if not c.get('asset'):continue
            a=c['asset'];path=asset_path(self.database,a)
            if not path.is_file() or path.stat().st_size!=a['size_bytes']:raise ValueError('RAW asset missing/size mismatch')
            h=hashlib.sha256()
            with path.open('rb') as f:
                for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
            if h.hexdigest()!=a['content_sha256']:raise ValueError('RAW asset hash mismatch')
            # Validate samples as well as shape before dispatch, without copying the file.
            data=np.memmap(path,dtype=np.uint8,mode='r')
            for i in range(a['frames']):decode(data,a['metadata'],i)
            del data
    def prepare(self,spec):
        from ade2e.apollo_lab import resolve_test,hydrate_test
        if set(spec.input)!={'test_ref'} or spec.comparison_kind!='single':raise ValueError('Apollo requires one immutable test reference')
        if any(v is not None for v in (spec.model,spec.sensor,spec.evaluator)):raise ValueError('External execution models are unsupported')
        with self.database.read_session() as session:snapshot=hydrate_test(session,resolve_test(session,spec.project_id,spec.input['test_ref']))
        if spec.seed!=snapshot['test']['seed']:raise ValueError('Seed must match saved revision')
        self.verify_assets(snapshot)
        if shutil.disk_usage(self.database.experiment_dir).free<1024**3:raise ValueError('Apollo needs 1 GiB free for bounded images/atomic publication')
        return snapshot
    def execute(self,snapshot,work_dir:Path,should_cancel):
        from ade2e.apollo_lab import asset_path
        from ade2e.adapters import AdapterCancelledBeforeDispatch
        self.verify_assets(snapshot)
        def checked():
            if should_cancel():raise AdapterCancelledBeforeDispatch('Apollo imaging cancelled')
        test=snapshot['test'];p=snapshot['profile']['config'];start=time.perf_counter()
        result=simulate(p,snapshot['channels'],test['captures'],test['seed'],should_cancel)
        result.update(test=test,profile=snapshot['profile'],channels=snapshot['channels'],adapter_version=self.metadata().version,seed=test['seed'],fidelity=FIDELITY)
        result['test_ref']={k:test[k] for k in ('id','revision','sha256')}
        artifacts=[];images=[]
        groups={}
        for j in result['jobs']:
            if j['frame'] is not None:groups.setdefault((j['channel'],j['frame']),[]).append(j)
        sources={c['channel']:c for c in snapshot['channels']}
        for (ch,frame),jobs in sorted(groups.items()):
            checked();c=sources[ch];m=c['raw']['metadata']
            if c.get('asset'):
                data=np.memmap(asset_path(self.database,c['asset']),dtype=np.uint8,mode='r')
                raw=decode(data,m,frame%c['asset']['frames']);del data
            else:raw=synthetic(m,ch,frame,test['seed'])
            stage_images,stats=process(raw,m,p)
            for j in jobs:
                checked();nodes={s['node'] for s in j['stages']};done=j['completed_ns'] is not None
                available=['RAW']+(['TFE'] if any(n.startswith('TFE') for n in nodes) or j.get('parent_job') else [])+(['OFE'] if 'OFE' in nodes else [])+(['IPE'] if 'IPE' in nodes else [])
                if not done and j['kind']=='snapshot':continue
                thumbw=400;thumbh=max(2,round(m['height']*thumbw/m['width']));sheet=Image.new('RGB',(thumbw*4,thumbh+28),'#152536');draw=ImageDraw.Draw(sheet)
                for i,(name,array) in enumerate(zip(['RAW','TFE','OFE','IPE'],stage_images)):
                    draw.text((i*thumbw+8,7),name+(' / reference' if name!='RAW' else ' / mosaic'),fill='white')
                    if name in available:sheet.paste(Image.fromarray(array).resize((thumbw,thumbh)),(i*thumbw,28))
                    else:draw.text((i*thumbw+8,50),'Not reached',fill='white')
                name=j['id']+'-stages.png';path=work_dir/name;sheet.save(path)
                artifacts.append(ExperimentOutputFile(path,name,'image/png'))
                record=dict(job_id=j['id'],channel=ch,frame=frame,source_index=frame%c['asset']['frames'] if c.get('asset') else frame,
                    source_sha256=c['asset']['content_sha256'] if c.get('asset') else stats['raw_sha256'],stage_sheet=name,available_stages=available,full_resolution=[m['width'],m['height']],thumbnail_tile=[thumbw,thumbh],**stats)
                if done and j['kind']=='snapshot':
                    name=j['id']+'-snapshot.png';path=work_dir/name;Image.fromarray(stage_images[-1]).save(path)
                    artifacts.append(ExperimentOutputFile(path,name,'image/png'));record['snapshot_image']=name
                images.append(record)
        result['images']=images
        result['pixel_implementation']=dict(version='cpu-reference/1',numpy=np.__version__,opencv=cv2.__version__,full_resolution_processing=True,display_only_downsampling=True)
        result['summary'].update(fidelity=FIDELITY,test_ref=result['test_ref'],cpu_wall_seconds=time.perf_counter()-start,image_count=len(images),adapter_version=self.metadata().version)
        for name,value in [('apollo-trace.json',result),('summary.json',result['summary']),('images.json',images)]:
            path=work_dir/name;path.write_bytes(canonical_bytes(value));artifacts.append(ExperimentOutputFile(path,name,'application/json'))
        path=work_dir/'frames.csv'
        with path.open('w',newline='') as f:
            fields=['id','kind','channel','frame','parent_job','source_ns','request_ns','received_ns','completed_ns','frame_age_ns','latency_ns','status','loss_reason']
            w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(result['jobs'])
        artifacts.append(ExperimentOutputFile(path,path.name,'text/csv'))
        return ExperimentExecution(summary=result['summary'],artifacts=artifacts,fidelity=FIDELITY)
