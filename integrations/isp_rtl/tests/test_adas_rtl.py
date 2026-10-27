"""Native four-channel protocol/pixel tests with camera timing independent of ready."""
from pathlib import Path
import csv
import subprocess

import numpy as np
import pytest

from axiom_isp_rtl import model
from axiom_isp_rtl.adas_design import generated_design
from axiom_isp_rtl.runtime import readiness


CHANNELS = [dict(channel=i, bayer_pattern=p, black_level=65,
                 gains=[173,131,115,211]) for i,p in enumerate(['RGGB','BGGR','GRBG','GBRG'])]
HARNESS = r'''
#include "Vaxiom_adas_top.h"
#include "verilated.h"
#include <fstream>
#include <cstdint>
#include <cstdlib>
Vaxiom_adas_top t;
std::ofstream pixels,events;
uint64_t cycle=0; unsigned errors=0;
unsigned frame_pixels[4][6]={};
uint64_t held_data[4]={}; uint64_t held_meta[4]={}; bool held[4]={};
uint64_t held_event_meta[4]={};bool held_event[4]={};
uint64_t bits(const VlWide<5>& a,unsigned start,unsigned n) {
 uint64_t v=uint64_t(a[start/32])>>(start%32);
 if(start%32+n>32)v|=uint64_t(a[start/32+1])<<(32-start%32);
 return v&((1ULL<<n)-1);
}
void tick() {
 t.clk=0;t.eval();
 for(unsigned c=0;c<4;++c) {
  uint64_t d=bits(t.out_data,c*36,36);
  uint64_t meta=((t.out_frame_id>>(c*16))&65535)|(((t.out_epoch>>(c*16))&65535)<<16)|
    (((t.out_config_version>>(c*16))&65535)<<32)|uint64_t((t.out_sof>>c)&1)<<48|uint64_t((t.out_eol>>c)&1)<<49;
  if(!t.reset&&held[c]&&(!((t.out_valid>>c)&1)||d!=held_data[c]||meta!=held_meta[c]))++errors;
  held[c]=!t.reset&&((t.out_valid>>c)&1)&&!((t.out_ready>>c)&1);held_data[c]=d;held_meta[c]=meta;
  uint64_t emeta=((t.event_frame_id>>(c*16))&65535)|(((t.event_epoch>>(c*16))&65535)<<16)|
   (((t.event_config_version>>(c*16))&65535)<<32)|uint64_t((t.event_reason>>(c*8))&255)<<48|uint64_t((t.event_kind>>c)&1)<<56;
  if(!t.reset&&held_event[c]&&(!((t.event_valid>>c)&1)||emeta!=held_event_meta[c]))++errors;
  held_event[c]=!t.reset&&((t.event_valid>>c)&1)&&!((t.event_ready>>c)&1);held_event_meta[c]=emeta;
  if(!t.reset&&((t.out_valid&t.out_ready)>>c&1)) {
   if((meta&65535)<6)++frame_pixels[c][meta&65535];
   pixels<<cycle<<','<<c<<','<<(meta&65535)<<','<<((meta>>16)&65535)<<','<<((meta>>32)&65535)<<','<<((d>>24)&4095)<<','<<((d>>12)&4095)<<','<<(d&4095)<<','<<((meta>>48)&1)<<','<<((meta>>49)&1)<<'\n';
  }
  if(!t.reset&&((t.event_valid&t.event_ready)>>c&1))
   events<<cycle<<','<<c<<','<<((t.event_frame_id>>(c*16))&65535)<<','<<((t.event_epoch>>(c*16))&65535)<<','<<((t.event_kind>>c)&1)<<','<<((t.event_reason>>(c*8))&255)<<'\n';
 }
 t.clk=1;t.eval();++cycle;
}
void write(unsigned addr,unsigned value,unsigned strobe=15,unsigned response=0) {
 t.s_axi_awaddr=addr;t.s_axi_awvalid=(addr&256)?1:0;t.s_axi_wdata=value;t.s_axi_wstrb=strobe;t.s_axi_wvalid=(addr&256)?0:1;
 bool aw=false,w=false;
 for(unsigned i=0;i<20;++i){if(i==3){if(!aw)t.s_axi_awvalid=1;if(!w)t.s_axi_wvalid=1;}t.clk=0;t.eval();bool a=t.s_axi_awready&&t.s_axi_awvalid,b=t.s_axi_wready&&t.s_axi_wvalid;tick();aw|=a;w|=b;if(a)t.s_axi_awvalid=0;if(b)t.s_axi_wvalid=0;if(aw&&w&&t.s_axi_bvalid){if(t.s_axi_bresp!=response)++errors;tick();return;}}
 ++errors;
}
unsigned read(unsigned addr) {
 t.s_axi_araddr=addr;t.s_axi_arvalid=1;bool sent=false;
 for(unsigned i=0;i<20;++i){t.clk=0;t.eval();bool a=t.s_axi_arready&&t.s_axi_arvalid;tick();if(a){sent=true;t.s_axi_arvalid=0;}if(sent&&t.s_axi_rvalid){unsigned value=t.s_axi_rdata;if(t.s_axi_rresp)++errors;tick();return value;}}
 ++errors;return 0;
}
int main(int argc,char**argv) {
 if(argc!=4)return 2;unsigned mode=std::atoi(argv[1]);pixels.open(argv[2]);events.open(argv[3]);
 pixels<<"cycle,ch,frame,epoch,version,r,g,b,sof,eol\n";events<<"cycle,ch,frame,epoch,commit,reason\n";
 t.reset=1;t.out_ready=15;t.event_ready=15;t.s_axi_bready=1;t.s_axi_rready=1;tick();tick();t.reset=0;
 for(unsigned c=0;c<4;++c)write(c*256,1);
 if(mode==13) {
  write(4,0xffff00a5,1);if(read(4)!=0xa5)++errors;
  write(4,0x0f00,2);if(read(4)!=0xfa5)++errors;
  write(4,0xf000,2,2);if(read(4)!=0xfa5)++errors;
  write(8,0xffff0055,1);write(8,0x1200,2);if(read(8)!=0x1255)++errors;
  write(8,0x10000,4,2);if(read(8)!=0x1255)++errors;
  write(0,0,0);if(read(0)!=1)++errors;
  write(5,0,15,2);write(0x400,0,15,2);write(28,0,15,2);
 }
 unsigned phase=0,x=0,y=0,frame=0,reset_phase=0;bool aw=false,w=false;
 for(unsigned local=0;local<(mode==7?125000:60000);++local) {
  t.cam_valid=0;t.cam_sof=0;t.cam_eol=0;t.out_ready=15;t.event_ready=(mode==4&&local<40000)?0:(mode==7&&local<85000)?14:15;
  if(((mode==1||mode==11)&&local>=5000&&local<8000)||((mode==3||mode==9)&&local>=5000&&local<15000))t.out_ready=14;
  if(mode==10&&frame_pixels[0][1]<124*92)t.event_ready=14;
  if((mode==2&&local==5000)||(mode==11&&local==5100)){t.s_axi_awaddr=0;t.s_axi_awvalid=1;t.s_axi_wdata=3;t.s_axi_wstrb=15;t.s_axi_wvalid=1;aw=false;w=false;}
  // A pending commit is an immutable snapshot; editing shadows afterwards
  // must not alter the pending frame configuration.
  if(mode==5&&(local==5000||local==5010||local==5020)) {
   t.s_axi_awaddr=local==5010?24:8;t.s_axi_awvalid=1;
   t.s_axi_wdata=local==5010?1:local==5000?256:300;t.s_axi_wstrb=15;t.s_axi_wvalid=1;
  }
  phase+=(mode==15?400:297);
  if(phase>=400) {
   phase-=400;
   if(frame<(mode==7?6:3)&&x<128&&y<96) {
    t.cam_valid=15;t.cam_sof=(x==0&&y==0)?15:0;t.cam_eol=x==127?15:0;
    t.cam_data=0;t.cam_frame_id=0;
    for(unsigned c=0;c<4;++c){unsigned v=(x*37+y*61+42+frame*257+c*97)%4096;t.cam_data|=uint64_t(v)<<(16*c);t.cam_frame_id|=uint64_t(frame)<<(16*c);}
   }
   if(++x==144){x=0;if(++y==100){y=0;++frame;}}
  }
  if(mode==6&&(local==5000||local==5001)){t.cam_valid|=1;t.cam_sof|=1;t.cam_eol&=14;}
  t.clk=0;t.eval();
  // Delay final beat by two cycles while AW/W and decoded actions are captured. The CSR reset
  // pulse then reaches the channel exactly in the finish-retirement cycle.
  if((mode==12||mode==14)&&reset_phase==0&&frame_pixels[0][0]==124*92-1&&(t.out_valid&1)&&!(t.out_frame_id&65535)) {
   t.out_ready=14;t.s_axi_awaddr=0;t.s_axi_awvalid=1;t.s_axi_wdata=3;t.s_axi_wstrb=15;t.s_axi_wvalid=1;reset_phase=1;
  }
  else if((mode==12||mode==14)&&reset_phase==1) {
   t.out_ready=14;reset_phase=2;
  }
  else if(mode==14&&reset_phase==2) {
   t.cam_valid|=1;t.cam_sof|=1;t.cam_eol&=14;reset_phase=3;
  }
  // Same-edge final RGB acceptance and unexpected SOF must retire as abort,
  // never commit then emit a second terminal. The following frame must recover.
  if(mode==8&&frame_pixels[0][0]==124*92-1&&(t.out_valid&1)&&!(t.out_frame_id&65535)) {
   t.cam_valid|=1;t.cam_sof|=1;t.cam_eol&=14;
  }
  // Framing has priority over overflow when both are observed together.
  if(mode==9&&(t.cam_valid&1)&&(t.fifo_levels&65535)==4096)t.cam_data|=0xf000;
  t.eval();bool a=t.s_axi_awready&&t.s_axi_awvalid,b=t.s_axi_wready&&t.s_axi_wvalid;tick();
  if(a){t.s_axi_awvalid=0;aw=true;}if(b){t.s_axi_wvalid=0;w=true;}
 }
 for(unsigned c=0;c<4;++c){
  unsigned peak=read(c*256+68);if(peak>4096)++errors;
  if(mode==3&&c==0&&(peak!=4096||read(c*256+44)!=1))++errors;
  if(mode==9&&c==0&&(read(44)!=0||read(48)!=1))++errors;
  if((mode==12||mode==14)&&read(c*256+72)!=(c==0?(mode==14?2u:1u):0u))++errors;
  if(mode==7&&read(c*256+60)!=(c==0?1u:0u))++errors;
 }
 if(mode==13){write(24,1);write(24,1,15,2);if(read(24)!=1)++errors;write(0,3);if(read(24)!=1)++errors;}
 return errors?5:0;
}
'''


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
    ready=readiness()
    if not ready['available']:pytest.skip('Native toolchain unavailable')
    p=tmp_path_factory.mktemp('adas_native')
    d=generated_design(dict(width=128,height=96,channels=CHANNELS),p)
    (p/'harness.cpp').write_text(HARNESS)
    r=subprocess.run([ready['verilator_path'],'--cc','--exe','--build','--top-module',d['top'],
        '-j','2','--Mdir',str(p/'obj'),*d['sources'],str(p/'harness.cpp')],capture_output=True,text=True,timeout=120)
    assert r.returncode==0,r.stdout+r.stderr
    return p/'obj/Vaxiom_adas_top'


@pytest.mark.parametrize('mode',range(16))
def test_native_fractional_input_stalls_reset_overflow(binary,tmp_path,mode):
    pixels=tmp_path/'pixels.csv';events=tmp_path/'events.csv'
    r=subprocess.run([str(binary),str(mode),str(pixels),str(events)],capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stdout+r.stderr
    with events.open() as f: ev=[{k:int(v) for k,v in row.items()} for row in csv.DictReader(f)]
    with pixels.open() as f: px=[{k:int(v) for k,v in row.items()} for row in csv.DictReader(f)]
    for ch in range(4):
        cfg=dict(width=128,height=96,frames=2,source_pattern='gradient',**{k:v for k,v in CHANNELS[ch].items() if k!='channel'})
        # Oracle is evaluated per source frame, including frames later aborted.
        for frame in range(6 if mode==7 else 3):
            x,y=np.meshgrid(np.arange(128),np.arange(96))
            raw=((x*37+y*61+42+frame*257+ch*97)%4096).astype('<u2')[None]
            frame_cfg={**cfg,'frames':1}
            if mode==5 and ch==0 and frame>0:frame_cfg['gains']=[256,131,115,211]
            expected=model.golden(raw,frame_cfg)['rgb'][0].reshape(-1,3)
            actual=[p for p in px if p['ch']==ch and p['frame']==frame]
            assert len(actual)<=len(expected)
            for i,p in enumerate(actual):
                assert [p['r'],p['g'],p['b']]==expected[i].tolist(),(mode,ch,frame,i,p,expected[i])
                assert p['sof']==int(i==0) and p['eol']==int(i%124==123)
                if mode==15 and i%124:assert p['cycle']==actual[i-1]['cycle']+1
                assert p['version']==int(mode==5 and ch==0 and frame>0)
                if mode in (12,14):assert p['epoch']==int(ch==0 and frame>0)*(2 if mode==14 else 1)
            terminal=[e for e in ev if e['ch']==ch and e['frame']==frame]
            if mode==7 and ch==0 and frame==4:
                assert not terminal and not actual
                continue
            assert len(terminal)==1,(mode,ch,frame,terminal)
            aborted=(ch==0 and frame==0 and mode in (2,3,6,8,9,11,14))
            assert terminal[0]['commit']==int(not aborted)
            if aborted:assert terminal[0]['reason']=={2:2,3:1,6:5,8:5,9:3,11:2,14:5}[mode]
            else:assert len(actual)==len(expected)


def test_generated_design_stimulus_independent_and_four_distinct_channels(tmp_path):
    config=dict(width=1920,height=1080,channels=CHANNELS)
    a=generated_design(config,tmp_path/'a')
    b=generated_design({**config,'frames':30,'stalls':[dict(channel=0,start_cycle=100,end_cycle=200)]},tmp_path/'b')
    assert a['design_hash']==b['design_hash']
    assert a['manifest']['line_ram_bits']==393216
    assert len(a['sources'])==12


def test_native_gain_product_extremes_with_stalled_pipeline(tmp_path):
    """Exercise RAW12 endpoints and complete Q7 gain range in actual RTL."""
    ready=readiness()
    if not ready['available']:pytest.skip('Native toolchain unavailable')
    channels=[{**c,'black_level':0,'gains':g} for c,g in zip(CHANNELS,
        ([0]*4,[128]*4,[65535]*4,[127,129,65535,0]))]
    design=generated_design(dict(width=128,height=96,channels=channels),tmp_path)
    harness=HARNESS.replace('(x*37+y*61+42+frame*257+c*97)%4096',
                            '(((x+y+frame)&1)?4095:0)')
    (tmp_path/'extremes.cpp').write_text(harness)
    result=subprocess.run([ready['verilator_path'],'--cc','--exe','--build',
        '--top-module',design['top'],'-j','2','--Mdir',str(tmp_path/'obj'),
        *design['sources'],str(tmp_path/'extremes.cpp')],capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    pixels=tmp_path/'pixels.csv';events=tmp_path/'events.csv'
    result=subprocess.run([str(tmp_path/'obj/Vaxiom_adas_top'),'1',str(pixels),str(events)],
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    with pixels.open() as f: rows=np.array([[int(v) for v in row] for row in list(csv.reader(f))[1:]])
    with events.open() as f: terminal=list(csv.DictReader(f))
    assert len(terminal)==12 and all(int(e['commit'])==1 for e in terminal)
    x,y=np.meshgrid(np.arange(128),np.arange(96))
    for ch,channel in enumerate(channels):
        for frame in range(3):
            raw=np.where((x+y+frame)&1,4095,0).astype('<u2')[None]
            expected=model.golden(raw,dict(width=128,height=96,frames=1,**channel))['rgb'][0].reshape(-1,3)
            actual=rows[(rows[:,1]==ch)&(rows[:,2]==frame),5:8]
            np.testing.assert_array_equal(actual,expected)
