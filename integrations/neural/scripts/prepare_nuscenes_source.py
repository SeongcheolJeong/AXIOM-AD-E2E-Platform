"""Prepare a bounded, original nuScenes research source; never acquires data.

Extract uses stdlib, build uses the optional neural runtime, and mcap uses the
local product environment. Recording anchors are supplied after normal import.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import zipfile


def canonical(v):
    return json.dumps(v, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''): h.update(data)
    return h.hexdigest()


def extract(args):
    root, out = Path(args.data_root), Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    tables = {name: json.loads((root / 'v1.0-mini' / (name + '.json')).read_bytes())
              for name in ('scene', 'sample', 'sample_data', 'calibrated_sensor', 'sensor', 'ego_pose')}
    by = {name: {x['token']: x for x in rows} for name, rows in tables.items()}
    scene = next(x for x in tables['scene'] if x['name'] == args.scene)
    def channel(r):
        return by['sensor'][by['calibrated_sensor'][r['calibrated_sensor_token']]['sensor_token']]['channel']
    rows = [r for r in tables['sample_data'] if by['sample'][r['sample_token']]['scene_token'] == scene['token']]
    cameras = sorted((r for r in rows if channel(r) == 'CAM_FRONT'), key=lambda r: r['timestamp'])[:args.frames]
    lidars = sorted((r for r in rows if channel(r) == 'LIDAR_TOP'), key=lambda r: r['timestamp'])
    if len(cameras) != args.frames or not 8 <= len(cameras) <= 150:
        raise ValueError('Expected 8..150 contiguous original camera records')
    selected = []
    for r in cameras:
        scan = min(lidars, key=lambda x: abs(x['timestamp'] - r['timestamp']))
        if abs(scan['timestamp'] - r['timestamp']) > 50000: raise ValueError('No LiDAR within 50ms')
        selected.append(dict(camera=r, lidar=scan))
    wanted = {r[k]['filename'] for r in selected for k in ('camera', 'lidar')}
    raw = out / 'raw'; raw.mkdir()
    for name in list(wanted):
        src = root / name
        if src.is_file():
            dst = raw / name; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dst); wanted.remove(name)
    with tarfile.open(args.archive, 'r|gz') as archive:
        for member in archive:
            name = member.name.removeprefix('./')
            if name not in wanted: continue
            if not member.isfile() or member.size > 8 * 1024 ** 2: raise ValueError('Invalid original member')
            dst = raw / name; dst.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, dst.open('wb') as target: shutil.copyfileobj(source, target)
            wanted.remove(name)
            if not wanted: break
    if wanted: raise ValueError('Missing original files: ' + repr(wanted))
    for name in tables:
        src = root / 'v1.0-mini' / (name + '.json'); shutil.copyfile(src, out / (name + '.json'))
    selection = dict(schema_version='neural-nuscenes-selection/1', scene_name=args.scene,
        scene_token=scene['token'], source_id=f'nuscenes-mini-{args.scene}-front-000-{len(cameras)-1:03d}-v1',
        frames=selected, metadata_sha256={name: sha(out / (name + '.json')) for name in tables},
        original_archive_sha256=sha(args.archive), use_scope='isolated_noncommercial_research')
    for row in selected:
        for kind in ('camera', 'lidar'): row[kind]['original_sha256'] = sha(raw / row[kind]['filename'])
    (out / 'selection.json').write_bytes(canonical(selection))
    print(json.dumps(dict(source_id=selection['source_id'], frames=len(cameras),
        start_time_ns=str(cameras[0]['timestamp']*1000), end_time_ns=str(cameras[-1]['timestamp']*1000))))


def build(args):
    import numpy as np
    from PIL import Image
    p = Path(args.output); selection = json.loads((p/'selection.json').read_bytes())
    by = {name: {r['token']:r for r in json.loads((p/(name+'.json')).read_bytes())}
          for name in ('calibrated_sensor','ego_pose')}
    def transform(r):
        w,x,y,z = r['rotation']; q=np.asarray([w,x,y,z],float); q/=np.linalg.norm(q); w,x,y,z=q
        m=np.eye(4); m[:3,:3]=[[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
            [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
            [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]; m[:3,3]=r['translation']; return m
    def native_pose(row): return transform(by['ego_pose'][row['ego_pose_token']]) @ transform(by['calibrated_sensor'][row['calibrated_sensor_token']])
    centers=np.array([native_pose(row['camera'])[:3,3] for row in selection['frames']]); origin=centers.mean(axis=0); scale=.05
    cv_to_gl=np.diag([1.,-1.,-1.,1.]); dataset=p/'dataset'; dataset.mkdir(); (dataset/'images').mkdir()
    first=selection['frames'][0]['camera']; start=first['timestamp']*1000
    k=np.asarray(by['calibrated_sensor'][first['calibrated_sensor_token']]['camera_intrinsic'],float)*.4; k[2,2]=1
    evidence=dict(schema_version='neural-dataset/2', source_kind='real_road', source_id=selection['source_id'],
        scene_name=selection['scene_name'],scene_token=selection['scene_token'],dataset='nuScenes',dataset_version='v1.0-mini',
        camera='CAM_FRONT', rig='nuScenes calibrated sensor rig',width=640,height=360,fps=12,
        coordinate_convention='right_handed_z_up_opengl_camera',world_origin_m=origin.tolist(),scale=scale,
        intrinsics=dict(fx=float(k[0,0]),fy=float(k[1,1]),cx=float(k[0,2]),cy=float(k[1,2]),width=640,height=360),
        source_start_time_ns=str(start),source_end_time_ns=str(selection['frames'][-1]['camera']['timestamp']*1000),
        source_use_scope=dict(use_scope='isolated_noncommercial_research',commercial_use=False,external_publication_cleared=False),
        license_scope='noncommercial_research_only',external_publication_cleared=False,
        metadata_sha256=selection['metadata_sha256'],original_archive_sha256=selection['original_archive_sha256'],
        image_transform=dict(operation='resize',input_width=1600,input_height=900,output_width=640,output_height=360,filter='LANCZOS'),frames=[])
    transforms=dict(camera_model='OPENCV',fl_x=float(k[0,0]),fl_y=float(k[1,1]),cx=float(k[0,2]),cy=float(k[1,2]),w=640,h=360,k1=0.,k2=0.,p1=0.,p2=0.,frames=[])
    points=[]; colors=[]
    for index,pair in enumerate(selection['frames']):
        cam,scan=pair['camera'],pair['lidar']; image_path=p/'raw'/cam['filename']
        if sha(image_path)!=cam['original_sha256']: raise ValueError('Original JPEG changed')
        with Image.open(image_path) as im:
            if im.size!=(1600,900): raise ValueError('Unexpected original image dimensions')
            rgb=np.array(im.convert('RGB').resize((640,360),Image.Resampling.LANCZOS))
        name=f'images/{index:06d}.png';Image.fromarray(rgb).save(dataset/name)
        c2w=native_pose(cam)@cv_to_gl;c2w[:3,3]=(c2w[:3,3]-origin)*scale
        transforms['frames'].append(dict(file_path=name,transform_matrix=c2w.tolist()))
        evidence['frames'].append(dict(frame_index=index,source_frame=index,sample_token=cam['sample_token'],
            sample_data_token=cam['token'],calibrated_sensor_token=cam['calibrated_sensor_token'],ego_pose_token=cam['ego_pose_token'],
            recording_ordinal=index,log_time_ns=str(cam['timestamp']*1000),time_s=(cam['timestamp']*1000-start)/1e9,
            original_image_path=cam['filename'],original_image_sha256=cam['original_sha256'],source_raw_sha256=cam['original_sha256'],
            file_path=name,png_sha256=sha(dataset/name),camera_to_world=c2w.tolist(),
            lidar_sample_data_token=scan['token'],lidar_original_sha256=scan['original_sha256'],lidar_camera_offset_us=scan['timestamp']-cam['timestamp']))
        cloud_path=p/'raw'/scan['filename']
        if sha(cloud_path)!=scan['original_sha256']: raise ValueError('Original LiDAR changed')
        cloud=np.fromfile(cloud_path,dtype='<f4').reshape(-1,5)[:,:3].astype(float);l2w=native_pose(scan)
        world=cloud@l2w[:3,:3].T+l2w[:3,3]; cp=native_pose(cam); local=(world-cp[:3,3])@cp[:3,:3]; depth=local[:,2]
        u=np.rint(k[0,0]*local[:,0]/np.maximum(depth,.001)+k[0,2]).astype(int);v=np.rint(k[1,1]*local[:,1]/np.maximum(depth,.001)+k[1,2]).astype(int)
        valid=np.isfinite(world).all(axis=1)&(depth>.5)&(depth<100)&(u>=0)&(u<640)&(v>=0)&(v<360)
        points.append((world[valid]-origin)*scale);colors.append(rgb[v[valid],u[valid]])
    xyz=np.concatenate(points);rgb=np.concatenate(colors);_,selected=np.unique(np.floor(xyz/(.08*scale)).astype('int64'),axis=0,return_index=True);selected=np.sort(selected)
    if len(selected)>100000:selected=selected[np.linspace(0,len(selected)-1,100000,dtype=int)]
    cloud=np.empty(len(selected),dtype=[('x','<f4'),('y','<f4'),('z','<f4'),('red','u1'),('green','u1'),('blue','u1')])
    for i,key in enumerate(('x','y','z')):cloud[key]=xyz[selected,i]
    for i,key in enumerate(('red','green','blue')):cloud[key]=rgb[selected,i]
    header=f'ply\nformat binary_little_endian 1.0\nelement vertex {len(cloud)}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n'
    (dataset/'points.ply').write_bytes(header.encode()+cloud.tobytes());transforms['ply_file_path']='points.ply'
    evidence['initialization']=dict(source='original_nuscenes_lidar_projected_with_recorded_sensor_and_ego_poses',points=len(cloud),sha256=sha(dataset/'points.ply'))
    (dataset/'transforms.json').write_bytes(canonical(transforms));(dataset/'neural-dataset.json').write_bytes(canonical(evidence))
    (p/'source-base.json').write_bytes(canonical(evidence));print(json.dumps(dict(frames=len(evidence['frames']),initial_points=len(cloud))))


def mcap(args):
    import base64
    from mcap.writer import Writer,CompressionType
    p=Path(args.output);s=json.loads((p/'selection.json').read_bytes());path=p/'original-front.mcap'
    schema={'title':'foxglove.CompressedImage','type':'object','properties':{'timestamp':{'type':'object'},'frame_id':{'type':'string'},'format':{'type':'string'},'data':{'type':'string','contentEncoding':'base64'}},'required':['timestamp','frame_id','format','data']}
    with path.open('xb') as f:
        w=Writer(f,compression=CompressionType.ZSTD,enable_crcs=True,enable_data_crcs=True);w.start(profile='axiom-neural-real-road/1',library='axiom-neural-nuscenes/1')
        sid=w.register_schema('foxglove.CompressedImage','jsonschema',canonical(schema));channel=w.register_channel('/camera/CAM_FRONT/image','json',sid,{'source':'original nuScenes camera JPEG','clock':'original_camera_capture_unix_ns','reactive':'false'})
        w.add_metadata('axiom.neural-source',{'source_id':s['source_id'],'scene_name':s['scene_name'],'source_kind':'real_road','use_scope':'isolated_noncommercial_research','commercial_use':'false','external_publication_cleared':'false'})
        for i,row in enumerate(s['frames']):
            c=row['camera'];raw=(p/'raw'/c['filename']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=c['original_sha256']:raise ValueError('Original JPEG changed')
            ns=c['timestamp']*1000;payload=dict(timestamp=dict(sec=ns//1000000000,nsec=ns%1000000000),frame_id='CAM_FRONT',format='jpeg',data=base64.b64encode(raw).decode())
            w.add_message(channel,ns,canonical(payload),publish_time=ns,sequence=i)
        w.finish()
    print(json.dumps(dict(path=str(path),sha256=sha(path),channel_id=channel,frames=len(s['frames']))))


def seal(args):
    p=Path(args.output);e=json.loads((p/'source-base.json').read_bytes());anchor=json.loads(Path(args.anchor).read_bytes())
    required={'recording_id','channel_id','recording_sha256'}
    if not required.issubset(anchor):raise ValueError('Actual imported recording identity required')
    e.update({k:anchor[k] for k in required});e['recording_sha256']=str(e['recording_sha256'])
    e.update(start_time_ns=e['source_start_time_ns'],end_time_ns=e['source_end_time_ns'],frame_count=len(e['frames']))
    # Record actual channel ordinals from the exact single-channel source MCAP.
    e['selection_sha256']=sha(p/'selection.json')
    (p/'dataset/neural-dataset.json').write_bytes(canonical(e))
    packed=p/'neural-real-road-dataset.zip'
    with zipfile.ZipFile(packed,'x',compression=zipfile.ZIP_STORED) as z:
        for f in sorted((p/'dataset').rglob('*')):
            if f.is_file():z.write(f,f.relative_to(p/'dataset').as_posix())
    source={**e,'schema_version':'neural-real-road-source/1','fidelity':'recorded_real_road','dataset_sha256':sha(packed)}
    source.pop('source_manifest_sha256',None)
    (p/'neural-real-road-source.json').write_bytes(canonical(source));print(json.dumps(dict(source_id=e['source_id'],manifest_sha256=sha(p/'neural-real-road-source.json'),dataset_sha256=sha(packed))))


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['extract','build','mcap','seal']);p.add_argument('--output',required=True);p.add_argument('--data-root');p.add_argument('--archive');p.add_argument('--scene',default='scene-0655');p.add_argument('--frames',type=int,default=96);p.add_argument('--anchor');p.add_argument('--research-only',action='store_true',required=True);args=p.parse_args();globals()[args.command](args)

if __name__=='__main__':main()
