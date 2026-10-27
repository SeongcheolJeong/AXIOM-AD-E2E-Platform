"""Single bounded native run over stdin/stdout. No core-platform imports required."""
import argparse,csv,hashlib,json,math,pathlib,re,subprocess,sys,tempfile,time

VERSION='0.1.0'
ENGINE_VERSION='v3.8.2'
ENGINE_BUILD='6400'
ARCHIVE_SHA256='bf9bfd637a76910910a1d12b7e2584e3637342d89ef6b6f4bcc041af505f8711'
LINUX_BINARY_SHA256='7f31d9a52a1da6c9d062a89f15b714f004308eb0d3c007107f09cce85afbf619'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def parse_csv(raw,expected_ids):
    lines=raw.splitlines()
    index=next((i for i,line in enumerate(lines) if line.startswith('Index [-], TimeStamp [s]')),None)
    if index is None:raise ValueError('Native CSV has no state header')
    reader=csv.DictReader(lines[index:]);frames=[]
    headers=[field.strip() for field in reader.fieldnames];reader.fieldnames=headers
    names=[field for field in headers if re.match(r'#\d+ Entity_Name',field)]
    fields={'x':'World_Position_X [m]','y':'World_Position_Y [m]','z':'World_Position_Z [m]',
      'heading_rad':'World_Heading_Angle [rad]','speed_mps':'Current_Speed [m/s]',
      'vx_mps':'Vel_X [m/s]','vy_mps':'Vel_Y [m/s]','ax_mps2':'Acc_X [m/s2]','ay_mps2':'Acc_Y [m/s2]',
      'bb_x':'bb_x [m]','bb_y':'bb_y [m]','length_m':'bb_length [m]','width_m':'bb_width [m]',
      'lane_id':'lane_id','lane_offset_m':'lane_offset[m]'}
    for row in reader:
        states=[]
        for name in names:
            prefix=name.split(' Entity_Name')[0]+' '
            state={'id':row[name].strip()}
            for key,column in fields.items():
                native_key=prefix+column
                # esmini 3.8.2 spells the first lane offset differently from later actors.
                if key=='lane_offset_m' and native_key not in row:native_key=prefix+'lane_offset [m]'
                value=float(row[native_key].strip())
                if not math.isfinite(value):raise ValueError('Nonfinite native state')
                state[key]=int(value) if key=='lane_id' else value
            states.append(state)
        if {s['id'] for s in states} != set(expected_ids):raise ValueError('Native actors differ from pinned scenario')
        stamp=float(row['TimeStamp [s]'])
        if not math.isfinite(stamp) or (frames and stamp<=frames[-1]['time_s']):raise ValueError('Native clock not strictly increasing')
        frames.append({'time_s':stamp,'actors':states})
    if len(frames)<2:raise ValueError('Native run produced fewer than two observations')
    return frames

def run(payload,binary):
    if set(payload) != {'schema_version','scenario_xml','road_xml','duration_s','timestep_s','seed','actor_ids'}:
        raise ValueError('Unsupported native run contract')
    if payload['schema_version']!='esmini-native-request/1':raise ValueError('Unsupported native request version')
    duration=float(payload['duration_s']);dt=float(payload['timestep_s']);seed=payload['seed']
    if not 1<=duration<=30 or not .02<=dt<=.2 or type(seed)!=int or not 0<=seed<=2147483647:
        raise ValueError('Out-of-bounds run timing/seed')
    binary=pathlib.Path(binary).resolve(strict=True)
    binary_hash=sha(binary.read_bytes())
    if sys.platform != 'linux' or binary_hash != LINUX_BINARY_SHA256:
        raise ValueError('This accepted profile requires the pinned official Linux v3.8.2 binary')
    version=subprocess.run([str(binary),'--version'],capture_output=True,text=True,timeout=10)
    version_text=version.stdout+version.stderr
    if 'esmini GIT TAG: v3.8.2' not in version_text or 'esmini BUILD VERSION: 6400' not in version_text:
        raise ValueError('Configured binary is not pinned esmini v3.8.2 build 6400')
    with tempfile.TemporaryDirectory(prefix='axiom-esmini-') as folder:
        work=pathlib.Path(folder)
        (work/'scenario.xosc').write_text(payload['scenario_xml'])
        (work/'road.xodr').write_text(payload['road_xml'])
        command=[str(binary),'--headless','--osc','scenario.xosc','--fixed_timestep',str(dt),'--csv_logger','trace.csv',
                 '--seed',str(seed),'--collision','--logfile_path','engine.log','--disable_stdout']
        started=time.perf_counter();result=subprocess.run(command,cwd=work,capture_output=True,text=True,timeout=60)
        elapsed=time.perf_counter()-started
        if result.returncode:raise RuntimeError('Native esmini failed: '+(result.stderr+result.stdout)[-3000:])
        csv_raw=(work/'trace.csv').read_bytes()
        if len(csv_raw)>16*1024*1024:raise ValueError('Native trace exceeds limit; nothing truncated')
        frames=parse_csv(csv_raw.decode(),payload['actor_ids'])
        if len(frames)>math.ceil(duration/dt)+4:raise ValueError('Native observations exceed pinned duration')
        if frames[-1]['time_s'] < duration-dt:raise ValueError('Native run ended before requested observation window')
        log=(work/'engine.log').read_text()
        if len(log.encode())>1024*1024:raise ValueError('Native log exceeds limit')
        return {'schema_version':'esmini-native-result/1','adapter_version':VERSION,'engine_version':ENGINE_VERSION,'engine_build':ENGINE_BUILD,
          'binary_sha256':binary_hash,'runner_sha256':sha(pathlib.Path(__file__).read_bytes()),
          'scenario_xml_sha256':sha(payload['scenario_xml'].encode()),'road_xml_sha256':sha(payload['road_xml'].encode()),
          'seed':seed,'requested_timestep_s':dt,'elapsed_s':elapsed,'frames':frames,'native_csv':csv_raw.decode(),'native_csv_sha256':sha(csv_raw),
          'native_log':log,'engine_stdout':result.stdout,'fidelity':'authored_object_kinematics','physical_sensor_validation':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True);args=parser.parse_args()
    raw=sys.stdin.buffer.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise ValueError('Native request exceeds 2 MiB')
    result=run(json.loads(raw),args.binary)
    sys.stdout.write(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))

if __name__=='__main__':main()
