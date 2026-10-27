"""Native product harness cross-checks against the separate NumPy image oracle."""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from axiom_isp_rtl import adas, model


@pytest.fixture(scope='module')
def native_identity():
    if not adas.readiness()['available']:
        pytest.skip('Native Verilator toolchain unavailable')
    return adas.identity()


def small_config():
    return dict(engine='adas_module_rtl', width=32, height=24, frames=3,
                raster_width=40, raster_height=32, pixel_rate_hz=74250000,
                max_cycles=10000, channels=[dict(channel=i,bayer_pattern=p,
                    source_pattern='gradient',black_level=65,gains=[173,131,115,211])
                    for i,p in enumerate(['RGGB','BGGR','GRBG','GBRG'])])


def oracle(config, channel, frame, gains=None):
    channel_config=config['channels'][channel]
    y,x=np.indices((config['height'],config['width']))
    raw=((x*37+y*61+(42+channel*101)%4096+frame*257)%4096).astype('<u2')
    cfg=dict(width=config['width'],height=config['height'],frames=1,
             bayer_pattern=channel_config['bayer_pattern'],black_level=channel_config['black_level'],
             gains=gains or channel_config['gains'])
    return raw,model.golden(raw[None],cfg)['rgb'][0]


def test_product_runner_all_cfa_hashes_and_real_previews(native_identity,tmp_path):
    config=small_config()
    result=adas.run(config,tmp_path,lambda:None,native_identity)
    assert result['summary']['verdict']=='passed'
    assert result['summary']['completed_frames']==12
    assert not result['summary']['acceptance_1080p30']
    assert result['summary']['source_window_ns']==51717
    assert result['summary']['drain_ns']==0
    for frame in result['frames']['frames']:
        raw,rgb=oracle(config,frame['channel'],frame['frame'])
        assert frame['input_sha256']==hashlib.sha256(raw.tobytes()).hexdigest()
        assert frame['output_sha256']==frame['reference_sha256']==hashlib.sha256(rgb.tobytes()).hexdigest()
        np.testing.assert_array_equal(np.asarray(Image.open(tmp_path/frame['output_preview_artifact'])),rgb>>4)
    assert (tmp_path/'waveform.vcd').stat().st_size<1024*1024
    assert len(result['samples'])<22000
    assert not list(tmp_path.glob('**/*.ppm'))


def test_pending_snapshot_survives_missing_sof(native_identity,tmp_path):
    config=small_config()
    source_sof=256+((40*32+1)*100000000+74250000-1)//74250000-1
    config.update(csr_updates=[dict(channel=0,at_cycle=500,gains=[256,128,128,128])],
                  faults=[dict(channel=0,kind='input_gap',at_cycle=source_sof,duration_cycles=1)])
    result=adas.run(config,tmp_path,lambda:None,native_identity)
    assert result['summary']['verdict']=='passed',result['summary']
    assert result['summary']['completed_frames']==11
    assert result['summary']['unadmitted_frames']==1
    assert result['summary']['missing_frames']==0
    frames=result['frames']['frames']
    assert frames[1]['status']=='unadmitted'
    assert frames[1]['loss_reason']=='input_sof_gap'
    assert frames[2]['config_version']==1
    for frame in frames:
        if frame['status']!='committed':continue
        gains=[256,128,128,128] if frame['channel']==0 and frame['frame']==2 else None
        _,rgb=oracle(config,frame['channel'],frame['frame'],gains)
        assert frame['output_sha256']==hashlib.sha256(rgb.tobytes()).hexdigest()


def test_local_reset_preserves_a_stalled_output_and_recovers(native_identity,tmp_path):
    config=small_config()
    config.update(width=128,height=96,raster_width=144,raster_height=100,max_cycles=65000,
                  faults=[dict(channel=0,kind='output_stall',at_cycle=5000,duration_cycles=3000),
                          dict(channel=0,kind='reset',at_cycle=5100,duration_cycles=1)])
    result=adas.run(config,tmp_path,lambda:None,native_identity)
    assert result['summary']['verdict']=='passed',result['summary']
    assert result['summary']['completed_frames']==11
    assert result['summary']['aborted_frames']==1
    frames=result['frames']['frames']
    assert frames[0]['loss_reason']=='channel_reset'
    assert frames[0]['received_pixels']>0
    assert frames[1]['status']==frames[2]['status']=='committed'
    assert all(f['status']=='committed' for f in frames if f['channel']!=0)
    consumer=json.loads((tmp_path/'consumer.json').read_text())
    assert consumer['published_frames']==11
    assert consumer['late_discarded_pixels']==1
    assert consumer['aborted_visible_frames']==consumer['protocol_error_count']==0
    assert not consumer['frames'][0]['visible']
    assert consumer['frames'][0]['discarded_pixels']==frames[0]['received_pixels']


def test_input_gap_checks_corrupt_partial_pixels_but_never_commits_them(native_identity,tmp_path):
    config=small_config()
    config.update(width=128,height=96,raster_width=144,raster_height=100,max_cycles=65000,
                  faults=[dict(channel=0,kind='input_gap',at_cycle=5000,duration_cycles=201)])
    result=adas.run(config,tmp_path,lambda:None,native_identity)
    summary=result['summary']
    assert summary['verdict']=='passed',summary
    assert summary['completed_frames']==11 and summary['aborted_frames']==1
    assert summary['committed_mismatch_count']==summary['unexpected_mismatch_count']==0
    assert summary['mismatch_count']==summary['expected_discarded_mismatch_count']
    assert result['frames']['frames'][0]['loss_reason']=='frame_boundary'
    assert result['frames']['frames'][1]['status']=='committed'
    partial=result['frames']['frames'][0]
    _,rgb=oracle(config,0,0)
    expected=rgb.reshape(-1,3)[:partial['received_pixels']].astype('<u2').tobytes()
    assert partial['reference_sha256']==hashlib.sha256(expected).hexdigest()
    assert result['summary']['consumer_published_frames']==11
    assert result['summary']['consumer_aborted_visible_frames']==0


def test_timeout_before_source_has_no_fabricated_raw_preview(native_identity,tmp_path):
    config=small_config()
    config.update(max_cycles=128,trace_windows=[dict(start_cycle=0,end_cycle=128)])
    result=adas.run(config,tmp_path,lambda:None,native_identity)
    assert result['summary']['verdict']=='timeout'
    assert all(f['source_pixels']==0 and not f['source_complete'] and f['input_preview_artifact'] is None
               for f in result['frames']['frames'])
    assert not list((tmp_path/'previews').iterdir())


def test_1800_frames_4096_faults_and_csr_have_bounded_previews(native_identity,tmp_path):
    config=small_config()
    config.update(width=16,height=16,frames=1800,raster_width=24,raster_height=24,
                  max_cycles=1500000,init_mode='randomized',init_seed=12345,
                  trace_windows=[])
    # Unsorted authoring is intentional. Each channel gets one update and one
    # one-cycle stall in each of 1024 frames; the cursors preserve cycle order.
    config['faults']=list(reversed([dict(channel=ch,kind='output_stall',
        at_cycle=256+f*576*100000000//74250000+250,duration_cycles=1)
        for f in range(1024) for ch in range(4)]))
    config['csr_updates']=list(reversed([dict(channel=ch,
        at_cycle=256+f*576*100000000//74250000+600,black_level=64)
        for f in range(1024) for ch in range(4)]))
    result=adas.run(config,tmp_path,lambda:None,native_identity)
    summary=result['summary']
    assert summary['verdict']=='passed',summary
    assert summary['completed_frames']==summary['consumer_published_frames']==7200
    assert summary['consumer_aborted_visible_frames']==summary['consumer_protocol_error_count']==0
    assert summary['preview_frames']==[0,3,29,1799]
    assert summary['preview_buffer_bytes']==4*4*(16*16+12*12)*3
    assert summary['raw_cache_bytes']==0
    assert summary['two_state'] and summary['init_mode']=='randomized'
    assert summary['init_seed']==12345
    assert len(list((tmp_path/'previews').glob('*.png')))==32
    frames=result['frames']['frames']
    assert len(frames)==7200
    assert all(f['output_sha256']==f['reference_sha256'] for f in frames)
    assert all(f['config_version']==1024 for f in frames if f['frame']==1799)
    assert all(bool(f['input_preview_artifact'])==(f['frame'] in [0,3,29,1799]) for f in frames)
    assert json.loads((tmp_path/'progress.json').read_text())['final']
    assert len(result['samples'])<=4000


def test_raw_streaming_cache_explicit_repeat_and_no_previews(native_identity,tmp_path):
    config=small_config()
    config.update(frames=40,max_cycles=100000,preview_frames=[],trace_windows=[])
    config['_input_raw_paths']={}
    for ch in range(2):
        channel=config['channels'][ch]
        channel.update(source_kind='raw_asset',raw_repeat=ch==0,
                       raw_asset_ref=dict(id=f'raw-{ch}',revision=1,sha256='a'*64))
        raw=[oracle(config,ch,frame)[0] for frame in range(1 if ch==0 else 40)]
        path=tmp_path/f'input-{ch}.raw';path.write_bytes(np.asarray(raw,dtype='<u2').tobytes())
        config['_input_raw_paths'][str(ch)]=str(path)
    result=adas.run(config,tmp_path/'run',lambda:None,native_identity)
    summary=result['summary']
    assert summary['verdict']=='passed',summary
    assert summary['completed_frames']==160 and summary['consumer_published_frames']==160
    assert summary['preview_frames']==[] and summary['preview_buffer_bytes']==0
    assert not list((tmp_path/'run/previews').iterdir())
    assert summary['raw_cache_bytes']<=2*2*config['width']*config['height']*2
    assert summary['raw_cache_frame_loads']==41
    consumers=json.loads((tmp_path/'run/consumer.json').read_text())['frames']
    for frame,consumer in zip(result['frames']['frames'],consumers):
        raw,rgb=oracle(config,frame['channel'],0 if frame['channel']==0 else frame['frame'])
        assert frame['input_sha256']==hashlib.sha256(raw.tobytes()).hexdigest()
        assert frame['output_sha256']==hashlib.sha256(rgb.tobytes()).hexdigest()
        assert consumer['published_sha256']==frame['output_sha256']
        assert consumer['visible'] and consumer['published_at_ns']==frame['terminal_ns']
    assert summary['source_repeat_policy'][0]['raw_repeat']
    assert not summary['source_repeat_policy'][1]['raw_repeat']


def test_randomized_initialization_and_64bit_budget_preserve_pixel_results(native_identity,tmp_path):
    config=small_config()
    config.update(init_mode='randomized',init_seed=987654,preview_frames=[],max_cycles=6100000000)
    source=adas.harness(config,42)
    assert 'LIMIT=6100000000ULL' in source
    result=adas.run(config,tmp_path,lambda:None,native_identity)
    assert result['summary']['verdict']=='passed',result['summary']
    for frame in result['frames']['frames']:
        _,rgb=oracle(config,frame['channel'],frame['frame'])
        assert frame['output_sha256']==hashlib.sha256(rgb.tobytes()).hexdigest()
    build=json.loads((tmp_path/'build.json').read_text())
    assert build['two_state'] and build['init_mode']=='randomized'
    assert build['command'][build['command'].index('--x-initial')+1]=='unique'
    assert result['summary']['cycles']<10000  # Budget is not the execution duration.


def test_transactional_consumer_rejects_incomplete_or_poisoned_commits(native_identity,tmp_path):
    """Compile the exact receiver implementation and inject invalid port sequences.

    This is a receiver negative control, separate from actual DUT execution tests.
    """
    import subprocess
    source=(Path(adas.__file__).parent/'templates/adas_harness.cpp').read_text()
    sha=source[source.index('struct Sha256 {'):source.index('constexpr unsigned W=')]
    consumer=source.split('// BEGIN TRANSACTIONAL CONSUMER')[1].split('// END TRANSACTIONAL CONSUMER')[0]
    consumer=consumer[consumer.index('struct Consumer {'):]
    code='\n'.join(['#include <array>','#include <cstdint>','#include <sstream>',
        '#include <iomanip>','#include <string>','#include <cassert>',sha,
        'constexpr unsigned OW=2,OH=2;',consumer,r'''
int main(){
 Consumer good;
 for(unsigned i=0;i<4;i++)assert(!good.pixel(0x1002003,7,11,i==0,i%2==1));
 assert(!good.finish(true,7,11,123));assert(good.visible&&good.publishedAt==123);
 Consumer shortFrame;assert(!shortFrame.pixel(0x1002003,7,11,true,false));
 assert(shortFrame.finish(true,7,11,123));assert(!shortFrame.visible);
 Consumer abort;assert(!abort.pixel(0x1002003,7,11,true,false));
 assert(!abort.finish(false,7,11,123));assert(!abort.pixel(0x1002003,7,11,false,true));
 assert(!abort.visible&&abort.discarded==2&&abort.lateDiscarded==1);
 assert(abort.finish(true,7,11,124));assert(!abort.visible);
 Consumer poison;
 for(unsigned i=0;i<4;i++)poison.pixel(0x1002003,i==1?8:7,11,i==0,i%2==1);
 assert(poison.finish(true,7,11,123));assert(!poison.visible);
 assert(good.finish(false,7,11,124));assert(good.abortedVisible);
}
'''])
    path=tmp_path/'consumer-negative-control.cpp';path.write_text(code)
    binary=tmp_path/'consumer-negative-control'
    subprocess.run([native_identity['tools']['compiler_path'],'-std=c++17',str(path),'-o',str(binary)],check=True,capture_output=True)
    subprocess.run([str(binary)],check=True,capture_output=True)
