"""Executable, bounded pre-board checks; no board-target readiness is inferred."""
import csv
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .runtime import ROOT, clean_env, digest, vendor_manifest
from .adas_design import generated_design

BOARD_ROOT=ROOT/'boards'/'ulx3s85f'


def readiness():
    result=dict(available=False,engines={},reason='Verilator, Icarus Verilog and vvp are required')
    for name,arg in [('verilator','--version'),('iverilog','-V'),('vvp','-V')]:
        path=shutil.which(name)
        row=dict(available=False,path=path,version=None)
        if path:
            try:
                version=subprocess.check_output([path,arg],stderr=subprocess.STDOUT,text=True,timeout=5,env=clean_env())
                row.update(available=True,version=version.splitlines()[0])
            except (OSError,subprocess.SubprocessError) as exc:row['error']=str(exc)
        result['engines'][name]=row
    try:vendor_manifest()
    except ValueError as exc:result['reason']=str(exc);return result
    result['available']=all(row['available'] for row in result['engines'].values())
    if result['available']:result['reason']='Native lint and four-state tools available; checks execute in the worker'
    return result


def identity():
    from axiom_rtl_lab import runner
    tools=readiness()
    if not tools['available']:raise ValueError(tools['reason'])
    sources={str(p.relative_to(ROOT)):digest(p) for p in sorted(ROOT.rglob('*'))
             if p.is_file() and p.suffix in {'.py','.cpp','.sv','.json','.md','.toml'}}
    binaries={str(Path(row['path']).resolve()):digest(Path(row['path']).resolve()) for row in tools['engines'].values()}
    icarus_lib=Path(tools['engines']['iverilog']['path']).resolve().parent.parent/'lib'/'ivl'
    if icarus_lib.is_dir():
        binaries.update({str(p):digest(p) for p in sorted(icarus_lib.iterdir()) if p.is_file()})
    return dict(adapter_version='0.1.0',tools=tools,source_sha256=sources,vendor=vendor_manifest(),
        shared_runner_sha256=digest(runner.__file__),
        tool_sha256=binaries,board_example_sha256={p.name:digest(p) for p in sorted(BOARD_ROOT.glob('*')) if p.is_file()},
        simulation_semantics='Icarus four-state digital simulation; Verilator static lint',two_state=False)


def validate(config):
    from ade2e.adas_module_schemas import AdasModuleConfig
    allowed={'engine','core_config','boundary_words','source_period_ns','sink_period_ns','reset_hold_cycles','lock_delay_cycles'}
    if set(config)-allowed or config.get('engine')!='adas_preboard_checks':raise ValueError('Unsupported pre-board configuration')
    result={**config,'core_config':AdasModuleConfig.model_validate(config['core_config']).model_dump()}
    for name,default,low,high in [('boundary_words',256,16,4096),('source_period_ns',14,4,40),
            ('sink_period_ns',10,4,40),('reset_hold_cycles',4,2,16),('lock_delay_cycles',8,2,32)]:
        value=config.get(name,default)
        if type(value) is not int or not low<=value<=high:raise ValueError('Invalid '+name)
        if name.endswith('period_ns') and value%2:raise ValueError('Clock period must be even integer ns')
        result[name]=value
    return result


def run(config,work_dir,check,tool_identity,seed=42):
    from axiom_rtl_lab.runner import run_process
    from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost
    c=validate(config);work_dir=Path(work_dir).resolve();work_dir.mkdir(parents=True,exist_ok=True)
    if not tool_identity.get('tools',{}).get('available'):raise ValueError('Native pre-board tools unavailable')
    tools={name:row['path'] for name,row in tool_identity['tools']['engines'].items()}
    check();design=generated_design(c['core_config'],work_dir)
    templates=('preboard_boundary.sv','preboard_boundary_tb.sv','preboard_core_init_tb.sv','preboard_blc_tb.sv','preboard_core_pixels_tb.sv')
    for name in templates:
        shutil.copyfile(ROOT/'templates'/name,work_dir/name)
    checks=[];commands=[]
    def result(name,status,engine,detail,**extras):
        checks.append(dict(id=name,status=status,engine=engine,detail=detail,**extras))
    def execute(args,stage,log,phase,timeout=90):
        commands.append(dict(phase=phase,args=args))
        run_process(args,stage,log,check,timeout,phase,clean_env())
    with tempfile.TemporaryDirectory(prefix='axiom-preboard-',dir='/tmp') as temp:
        stage=Path(temp)
        for source in design['sources']:shutil.copyfile(source,stage/Path(source).name)
        for name in templates:
            shutil.copyfile(work_dir/name,stage/name)
        sources=[Path(source).name for source in design['sources']]
        lint=work_dir/'lint.log'
        try:
            execute([tools['verilator'],'--lint-only','-Wall','-Wno-fatal','--top-module',design['top'],*sources],stage,lint,'core_lint')
            warnings=re.findall(r'%Warning-([A-Z0-9_]+)',lint.read_text())
            critical=sorted(set(warnings)&{'LATCH','MULTIDRIVEN','UNOPTFLAT','IMPLICIT','PINMISSING'})
            undriven=re.findall(r'%Warning-UNDRIVEN: ([^\n]+)',lint.read_text())
            waived=[]
            for warning in undriven:
                inactive=warning.startswith('axiom_adas_blc.sv:') and any("'pipe_0_pixel_"+str(i)+"_wire'" in warning for i in (1,2,3))
                padding=(warning.startswith('axiom_adas_demosaic.sv:') and "'pipe_4_tdata_comb'[39:36]" in warning) or (warning.startswith('axiom_adas_linebuffer.sv:') and "'line_data_out'[63:60,47:44,31:28,15:12]" in warning)
                if inactive or padding:waived.append(warning)
                else:critical.append('UNDRIVEN: '+warning)
            result('generated_core_lint','failed' if critical else 'passed','Verilator',
                'Current generated ISP source lint; only unused PPC lanes and masked bus padding waive UNDRIVEN',warnings={key:warnings.count(key) for key in sorted(set(warnings))},critical_warnings=critical,undriven_waivers=waived)
        except (AdapterCancelled,AdapterOwnershipLost):raise
        except RuntimeError as exc:result('generated_core_lint','failed','Verilator',str(exc)[-2000:])
        core_log=work_dir/'core-init.log'
        try:
            execute([tools['iverilog'],'-g2012','-s','preboard_core_init_tb','-o','core-init.vvp',*sources,'preboard_core_init_tb.sv'],stage,work_dir/'core-init-compile.log','core_init_compile')
            execute([tools['vvp'],'core-init.vvp'],stage,core_log,'core_init_4state')
            require='CORE_INIT_RESULT errors=0 startup_unknown=1'
            result('core_control_init_4state','passed' if require in core_log.read_text() else 'failed','Icarus Verilog',
                'Actual generated core: unknown control state before POR, reset initializes control/valid/counters; no pixel-data X-propagation coverage')
        except (AdapterCancelled,AdapterOwnershipLost):raise
        except RuntimeError as exc:result('core_control_init_4state','failed','Icarus Verilog',str(exc)[-2000:])
        for name,module,negative in [('blc_fixed_4state','axiom_adas_blc',False),('blc_original_x_control','blc',True)]:
            log=work_dir/(name+'.log');error=None
            try:
                execute([tools['iverilog'],'-g2012','-s','preboard_blc_tb','-DBLC_MODULE='+module,'-o',name+'.vvp',module+'.sv','preboard_blc_tb.sv'],stage,work_dir/(name+'-compile.log'),name+'_compile')
                execute([tools['vvp'],name+'.vvp'],stage,log,name)
            except (AdapterCancelled,AdapterOwnershipLost):raise
            except RuntimeError as exc:error=str(exc)
            text=log.read_text() if log.exists() else ''
            passed=(bool(error) and 'BLC_VALID_DATA_X' in text and 'BLC_RESULT errors=' in text) if negative else (not error and 'BLC_RESULT errors=0 pixels=3' in text)
            result(name,'passed' if passed else 'failed','Icarus Verilog',
                'Original pinned upstream BLC has valid-data X; negative control reproduces and detects it' if negative else 'Zero-extended derivative gives known exact RAW-minus-black results for 0,1000,4095',negative_control=negative)
        small=stage/'core-pixels';small.mkdir()
        probe_config={**c['core_config'],'width':16,'height':16}
        probe=generated_design(probe_config,small)
        shutil.copyfile(work_dir/'preboard_core_pixels_tb.sv',small/'preboard_core_pixels_tb.sv')
        shutil.copyfile(small/'design.json',work_dir/'core-pixel-probe-design.json')
        try:
            execute([tools['iverilog'],'-g2012','-s','preboard_core_pixels_tb','-o','core-pixels.vvp',
                *[Path(s).name for s in probe['sources']],'preboard_core_pixels_tb.sv'],small,work_dir/'core-pixels-compile.log','core_pixels_compile')
            execute([tools['vvp'],'core-pixels.vvp'],small,work_dir/'core-pixels.log','core_pixels_4state')
            good='CORE_PIXELS_RESULT errors=0 pixels=144,144,144,144' in (work_dir/'core-pixels.log').read_text()
            result('core_valid_pixels_4state','passed' if good else 'failed','Icarus Verilog',
                'Actual generated four-channel ISP at 16x16: 576 accepted RGB words have no X/Z and four commit terminals; reduced functional probe, not full-HD X coverage',probe_design_hash=probe['design_hash'])
        except (AdapterCancelled,AdapterOwnershipLost):raise
        except RuntimeError as exc:result('core_valid_pixels_4state','failed','Icarus Verilog',str(exc)[-2000:])
        params=dict(WORDS=c['boundary_words'],SOURCE_PERIOD=c['source_period_ns'],SINK_PERIOD=c['sink_period_ns'],
                    HOLD=c['reset_hold_cycles'],LOCK_DELAY=c['lock_delay_cycles'],SEED=seed)
        for name,bad_reset,bad_consumer,marker in [('boundary_4state',0,0,None),('bad_reset_control',1,0,'BAD_RESET_DETECTED'),
                ('bad_consumer_control',0,1,'BAD_CONSUMER_DETECTED')]:
            log=work_dir/(name+'.log');native_error=None
            args=[tools['iverilog'],'-g2012','-s','preboard_boundary_tb','-o',name+'.vvp',
                *[f'-Ppreboard_boundary_tb.{key}={value}' for key,value in dict(**params,BAD_RESET=bad_reset,BAD_CONSUMER=bad_consumer).items()],
                'preboard_boundary.sv','preboard_boundary_tb.sv']
            try:
                execute(args,stage,work_dir/(name+'-compile.log'),name+'_compile')
                execute([tools['vvp'],name+'.vvp'],stage,log,name)
            except (AdapterCancelled,AdapterOwnershipLost):raise
            except RuntimeError as exc:native_error=str(exc)
            text=log.read_text() if log.exists() else ''
            match=re.search(r'PREBOARD_RESULT errors=(\d+) startup_unknown=(\d+) reset_checks=(\d+) consumer_checks=(\d+) words_after_relock=(\d+) received_before_loss=(\d+) pending_at_loss=(\d+)',text)
            if marker:
                passed=bool(native_error and marker in text and match and int(match[1])>0)
                result(name,'passed' if passed else 'failed','Icarus Verilog',
                    'Deliberately faulty reset-release detected' if bad_reset else 'Oracle self-test detects publish-before-commit; not an actual ISP consumer implementation',negative_control=True)
            else:
                passed=bool(not native_error and match and match[1]=='0' and match[2]=='1' and int(match[5])==c['boundary_words'] and int(match[7])>0)
                result(name,'passed' if passed else 'failed','Icarus Verilog',
                    'Separate 32-bit depth8 Gray-pointer FIFO, independent clocks, two reset/lock epochs, seeded sink stalls; digital CDC exercise, not metastability proof',
                    received_words=int(match[5])+int(match[6]) if match else 0,pending_words_discarded_on_lock_loss=int(match[7]) if match else 0,
                    recovered_words=int(match[5]) if match else 0,clock_periods_ns=[c['source_period_ns'],c['sink_period_ns']])
                for artifact in ('native.csv','boundary.vcd'):
                    if (stage/artifact).exists():shutil.copyfile(stage/artifact,work_dir/artifact)
    skeleton='''// Integration skeleton only: no board pins, PLL instance, PHY, camera bridge or ISP wiring.
module axiom_board_boundary_skeleton(input wire logic_clk, source_clk, por_n, pll_locked,
 output wire logic_reset,source_reset);
 axiom_reset_boundary logic_boundary(logic_clk,por_n,pll_locked,logic_reset);
 axiom_reset_boundary source_boundary(source_clk,por_n,pll_locked,source_reset);
endmodule
'''
    (work_dir/'board-top-skeleton.sv').write_text(skeleton)
    requirements={key:'not_configured' for key in ('board_part_package_speed','pinmap','pll_clock_constraints','csi_phy','io_timing','power_sequence','external_memory','physical_hil')}
    board_example=None
    if tool_identity.get('board_example_sha256'):
        folder=work_dir/'board-example';folder.mkdir()
        for name,sha in tool_identity['board_example_sha256'].items():
            source=BOARD_ROOT/name
            if not source.is_file() or digest(source)!=sha:raise ValueError('Board example identity changed: '+name)
            shutil.copyfile(source,folder/name)
        board_example=json.loads((folder/'profile.json').read_text())
    manifest=dict(schema_version='adas-preboard-boundary/1',core_design_hash=design['design_hash'],core_config=c['core_config'],
        reference_only=True,integrated_with_isp=False,source_period_ns=c['source_period_ns'],sink_period_ns=c['sink_period_ns'],
        cdc_assumptions=['Both domains share asynchronous reset assertion; release uses two local clock edges',
            'Gray pointer bits use two-stage synchronizers; physical placement/skew constraints are absent',
            'Digital simulation does not model metastability or establish CDC/RDC signoff'],
        external_contracts=dict(post_csi='Four RAW12 logical streams with SOF/EOL/frame_id; no source backpressure; PHY/ECC/CRC/lane/VC mapping required upstream',
            host_axi='Single logic-clock AXI4-Lite; AW/W independently captured, B/R held until accepted; actual host CDC bridge not connected',
            downstream='RGB12 beats remain provisional until matching frame/epoch/config terminal commit; abort invalidates whole partial frame; consumer must retain metadata and honor ready'),
        board_requirements=requirements,board_example=board_example,tool_identity=tool_identity,commands=commands)
    (work_dir/'boundary-manifest.json').write_text(json.dumps(manifest,indent=2))
    (work_dir/'checks.json').write_text(json.dumps(checks,indent=2))
    samples=[]
    if (work_dir/'native.csv').exists():
        with (work_dir/'native.csv').open() as stream:
            rows=list(csv.DictReader(stream))
        stride=max(1,(len(rows)+2047)//2048)
        for row in rows[::stride]:
            if all(value not in ('x','z') for value in row.values()):
                samples.append(dict(t=int(row['time_ps'])/1000,values={k:int(v) for k,v in row.items() if k!='time_ps'}))
    summary=dict(engine='adas_preboard_checks',operation='preboard_checks',verdict='passed' if all(x['status']=='passed' for x in checks) else 'failed',
        design_hash=design['design_hash'],checks=checks,board_requirements=requirements,board_example=board_example,board_ready=False,
        scope='Static ISP lint/control initialization, reduced-geometry core valid-pixel X checks and independent reference clock/reset/CDC tests; no board, PHY, full-HD X-propagation campaign or physical CDC signoff')
    (work_dir/'summary.json').write_text(json.dumps(summary,indent=2));check()
    signals=[dict(id=key,label=key,kind='digital') for key in ('write_reset','read_reset','sent','received','valid','ready','epoch')]
    return dict(summary=summary,axis=dict(kind='time',unit='ns'),samples=samples,signals=signals)
