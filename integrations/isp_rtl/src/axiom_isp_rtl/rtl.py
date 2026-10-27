"""Real Verilator execution of pinned open ISP blocks and an AXIOM frame barrier."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image
from axiom_rtl_lab.runner import run_process

from . import model
from .runtime import ROOT, VENDOR, clean_env, digest, identity, readiness, vendor_manifest

SOURCE_NAMES=['blc.sv','colorgain.sv','demosaic.sv','linebuffer.sv','simple_dual_ported_ram.sv','skidbuffer.sv']


def generated_design(config, outdir):
    c=model.validate(config);p=Path(outdir);p.mkdir(parents=True,exist_ok=True)
    manifest=vendor_manifest()
    text=(ROOT/'templates/top.sv').read_text()
    green_red_first=c['bayer_pattern'] in {'RGGB','GRBG'}
    substitutions=dict(WIDTH=c['width'],HEIGHT=c['height'],
        CFA={'BGGR':0,'GBRG':1,'GRBG':2,'RGGB':3}[c['bayer_pattern']],
        BLACK=c['black_level'],RGAIN=c['gains'][0],BGAIN=c['gains'][3],
        G0GAIN=c['gains'][1 if green_red_first else 2],
        G1GAIN=c['gains'][2 if green_red_first else 1],
        MAX_RES=2**(c['width']-1).bit_length())
    for key,value in substitutions.items():text=text.replace('@'+key+'@',str(value))
    (p/'axiom_isp_top.sv').write_text(text)
    for name in SOURCE_NAMES:shutil.copyfile(VENDOR/name,p/name)
    shutil.copyfile(VENDOR/'LICENSE.md',p/'mini-isp-LICENSE.md')
    shutil.copyfile(VENDOR/'REUSE.toml',p/'mini-isp-REUSE.toml')
    sources=[p/'axiom_isp_top.sv',*[p/n for n in SOURCE_NAMES]]
    hashes={s.name:digest(s) for s in sources}
    design_hash=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    receipt=dict(schema_version='isp-generated-design/1',top='axiom_isp_top',
                 design_hash=design_hash,sources=hashes,vendor=manifest,
                 pixel_clock_hz=100000000,pixels_per_cycle=1,
                 input_format='RAW12 in 16-bit AXIS beats',
                 output_format='RGB12 packed R[35:24],G[23:12],B[11:0]',
                 frame_barrier='One synchronous frame RAM; next frame waits until RGB output drains',
                 output_roi=dict(x=2,y=2,width=c['width']-4,height=c['height']-4))
    (p/'design.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return dict(sources=[str(s.resolve()) for s in sources],top='axiom_isp_top',design_hash=design_hash,manifest=receipt)


def _harness(c):
    replacements=dict(WORDS=c['width']*c['height']*c['frames'],FRAME_WORDS=c['width']*c['height'],
        OUT_WORDS=(c['width']-4)*(c['height']-4),OUT_WIDTH=c['width']-4,
        WIDTH=c['width'],FRAMES=c['frames'],MAX_CYCLES=c['max_cycles'],
        RESET=c['reset_at_cycle'] if c['reset_at_cycle'] is not None else 4294967295,
        STRIDE=max(1,(c['max_cycles']+11999)//12000),
        STALLS=' || '.join(f'(cycle>={s["start_cycle"]}u && cycle<{s["end_cycle"]}u)' for s in c['stalls']) or 'false')
    text=(ROOT/'templates/harness.cpp').read_text()
    for key,value in replacements.items():text=text.replace('@'+key+'@',str(value))
    return text,replacements['STRIDE']


def _images(p,raw,reference,actual,valid_mask,c):
    Image.fromarray((raw[0]>>4).astype(np.uint8),'L').save(p/'raw-preview.png')
    Image.fromarray((reference[0]>>4).astype(np.uint8),'RGB').save(p/'reference.png')
    Image.fromarray((actual[0]>>4).astype(np.uint8),'RGB').save(p/'rtl.png')
    diff=np.abs(reference[0].astype(np.int32)-actual[0].astype(np.int32))
    Image.fromarray(np.clip(diff,0,255).astype(np.uint8),'RGB').save(p/'diff.png')
    images=dict(schema_version='isp-images/1',frame_index=0,
        input_sha256=digest(p/'input.raw'),output_shape=[c['height']-4,c['width']-4,3],
        input_shape=[c['height'],c['width']],bayer_pattern=c['bayer_pattern'],
        valid_output_pixels=int(valid_mask[0].sum()),output_pixels=int(valid_mask[0].size),
        roi_origin=[2,2],display='RGB12 right shift 4; no automatic contrast scaling',
        difference_display='absolute RAW12-code error, saturated at 255; invalid output displayed black',
        files=dict(raw='raw-preview.png',rtl='rtl.png',reference='reference.png',diff='diff.png'))
    (p/'images.json').write_text(json.dumps(images,indent=2)+'\n')


def run(config,work_dir,check,tool_identity,seed=42):
    c=model.validate(config);p=Path(work_dir).resolve();p.mkdir(parents=True,exist_ok=True);check()
    if c['source_kind']=='raw_asset':
        path=Path(c.get('_input_raw_path',''))
        expected=c['frames']*c['width']*c['height']*2
        if not path.is_file() or path.stat().st_size!=expected:
            raise ValueError('Decoded immutable RAW asset is missing or has the wrong byte count')
        raw=np.frombuffer(path.read_bytes(),dtype='<u2').copy().reshape(c['frames'],c['height'],c['width'])
    else:raw=model.synthetic(c,seed)
    golden=model.golden(raw,c); reference=golden['rgb'];raw.tofile(p/'input.raw');reference.tofile(p/'reference.raw')
    design=generated_design(c,p);harness,stride=_harness(c);(p/'harness.cpp').write_text(harness)
    from .build import write_vendor_templates
    write_vendor_templates(p,design['sources'])
    stage=Path(tempfile.mkdtemp(prefix='axiom-isp-',dir='/tmp'))
    def bounded_check():
        check()
        for f in [stage/'compile.log',p/'native.csv',p/'pixels.csv',p/'waveform.vcd',p/'run.log']:
            if f.exists() and f.stat().st_size>96*1024*1024:
                raise ValueError('ISP native artifact exceeded 96 MiB budget')
    try:
        for path in design['sources']:shutil.copyfile(path,stage/Path(path).name)
        (stage/'harness.cpp').write_text(harness)
        tools=tool_identity.get('tools',tool_identity)
        args=[tools['verilator_path'],'--cc','--exe','--build','--assert',
              '--top-module','axiom_isp_top','--prefix','Vaxiom_isp_top','-j','2',
              '--x-initial','0','--x-assign','0',
              '-MAKEFLAGS','CXX='+tools['compiler_path'],'--Mdir','obj',
              *[Path(s).name for s in design['sources']],'harness.cpp']
        try:run_process(args,stage,stage/'compile.log',bounded_check,120,'isp_compile',clean_env())
        finally:
            if (stage/'compile.log').exists():shutil.copyfile(stage/'compile.log',p/'compile.log')
        binary=stage/'obj/Vaxiom_isp_top'
        (p/'build.json').write_text(json.dumps(dict(command=args,design_hash=design['design_hash'],
            binary_sha256=digest(binary),harness_sha256=digest(stage/'harness.cpp'),
            tool_identity=tool_identity,two_state=True),indent=2)+'\n')
        run_process([str(binary),str(p/'input.raw'),str(p/'native.csv'),str(p/'pixels.csv'),
                     str(p/'native-summary.json'),str(p/'waveform.vcd')],stage,p/'run.log',
                    bounded_check,60,'isp_simulation',clean_env())
        stats=json.loads((p/'native-summary.json').read_text())
        with (p/'native.csv').open() as f:samples=[{k:int(v) for k,v in row.items()} for row in csv.DictReader(f)]
        with (p/'pixels.csv').open() as f:outputs=[{k:int(v) for k,v in row.items()} for row in csv.DictReader(f)]
        if not samples or len(samples)>17000:raise ValueError('Native ISP trace sample budget exceeded')
        actual=np.zeros_like(reference);mask=np.zeros(reference.shape[:-1],dtype=bool)
        epochs={};errors=[];mismatch_count=0;max_error=0; w,h=c['width']-4,c['height']-4
        for row in outputs:
            frame,index=row['frame'],row['index']
            if frame>=c['frames'] or index>=w*h:continue # Native protocol checker records out-of-range output.
            if epochs.get(frame)!=row['epoch']:
                actual[frame]=0;mask[frame]=False;epochs[frame]=row['epoch']
            y,x=divmod(index,w); values=[row['r'],row['g'],row['b']]
            actual[frame,y,x]=values;mask[frame,y,x]=True
            for ch,value in enumerate(values):
                expected=int(reference[frame,y,x,ch])
                max_error=max(max_error,abs(value-expected))
                if value!=expected:
                    mismatch_count+=1
                    if len(errors)<100:errors.append(dict(frame=frame,x=x,y=y,channel='RGB'[ch],
                        expected=expected,actual=value,cycle=row['cycle'],time=row['time']))
        actual.tofile(p/'output.raw');_images(p,raw,reference,actual,mask,c)
        complete=stats['completed_frames']==c['frames'] and bool(mask.all())
        verdict='failed' if mismatch_count or stats['protocol_error_count'] else 'passed' if complete else 'timeout'
        summary=dict(**stats,verdict=verdict,mismatch_count=mismatch_count,first_mismatch=errors[0] if errors else None,
            input_sha256=digest(p/'input.raw'),output_sha256=digest(p/'output.raw'),reference_sha256=digest(p/'reference.raw'),
            width=c['width'],height=c['height'],output_width=w,output_height=h,requested_frames=c['frames'],
            cropped_border=2,roi_origin=[2,2],bayer_pattern=c['bayer_pattern'],data_checked=True,
            data_loss_free=complete and not stats['protocol_error_count'],design_hash=design['design_hash'],
            sample_stride=stride,dense_trace_cycles=4096,vcd_cycles=stats['cycles'],
            vcd_scope='Actual top-level ports at both clock phases for the full execution',
            sampling='Actual native pre-edge samples: first 4096 cycles dense, then fixed stride plus first output and phase transitions; pixel records complete',
            expectation='Independent integer RAW12 BLC, Bayer gains, and MHC convolution',
            validation_scope='Functional two-state RTL simulation; no Qualcomm equivalence, board or timing validation',
            max_abs_error=max_error,unreceived_pixels=int(mask.size-mask.sum()),
            pixel_error_scope='All accepted output pixels, including partial output discarded on reset; missing pixels are counted separately',errors=errors)
        (p/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        (p/'pixel-errors.json').write_text(json.dumps(errors,indent=2)+'\n')
        signals=[dict(id=k,unit='code' if k.endswith(('data','_r','_g','_b')) else 'pixels' if k=='buffer_level' else 'bit')
                 for k in samples[0] if k not in {'cycle','time'}]
        return dict(axis=dict(unit='ns',label='Simulated 100 MHz clock'),signals=signals,samples=samples,
                    summary=summary,images=json.loads((p/'images.json').read_text()))
    finally:shutil.rmtree(stage)
