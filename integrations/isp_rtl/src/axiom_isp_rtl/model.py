"""Independent integer image oracle, expressed as spatial convolution kernels.

MHC kernels use a denominator of 16, with truncation and clipping after
interpolation. This is deliberately separate from the RTL pipeline scheduling.
"""
import numpy as np

PATTERNS = {'RGGB': (('R', 'Gr'), ('Gb', 'B')),
            'BGGR': (('B', 'Gb'), ('Gr', 'R')),
            'GRBG': (('Gr', 'R'), ('B', 'Gb')),
            'GBRG': (('Gb', 'B'), ('R', 'Gr'))}
GAIN_INDEX = {'R': 0, 'Gr': 1, 'Gb': 2, 'B': 3}


def validate(config):
    c = dict(config)
    defaults = dict(engine='isp_rtl', pipeline_id='mini-isp-blc-wb-mhc/1', width=128,
                    height=96, bayer_pattern='RGGB', black_level=64,
                    gains=[128]*4, frames=1, source_kind='synthetic',
                    source_pattern='color_bars', stalls=[], reset_at_cycle=None,
                    max_cycles=200000)
    for k, v in defaults.items(): c.setdefault(k, v)
    for key, low, high in [('width',8,320),('height',8,240),('frames',1,2),
                          ('black_level',0,4095),('max_cycles',16,1000000)]:
        if type(c[key]) is not int or not low <= c[key] <= high:
            raise ValueError(f'Invalid ISP {key}')
    if c['engine'] != 'isp_rtl' or c['pipeline_id'] != defaults['pipeline_id']:
        raise ValueError('Unsupported ISP pipeline')
    if c['width'] % 2 or c['height'] % 2 or c['bayer_pattern'] not in PATTERNS:
        raise ValueError('ISP requires even dimensions and a supported Bayer pattern')
    if len(c['gains']) != 4 or any(type(v) is not int or not 0 <= v <= 65535 for v in c['gains']):
        raise ValueError('ISP gains must be four uint16 values in R,Gr,Gb,B order')
    if c['source_kind'] not in {'synthetic','raw_asset'} or c['source_pattern'] not in {'gradient','color_bars','impulse'}:
        raise ValueError('Unsupported ISP input source')
    if len(c['stalls']) > 32:
        raise ValueError('Too many stall intervals')
    previous_end=0
    for s in c['stalls']:
        a,b=s['start_cycle'],s['end_cycle']
        if type(a) is not int or type(b) is not int or not previous_end <= a < b <= c['max_cycles']:
            raise ValueError('Stalls must be sorted, disjoint nonempty intervals inside the cycle budget')
        previous_end=b
    r=c['reset_at_cycle']
    if r is not None and (type(r) is not int or not 2 <= r < c['max_cycles']):
        raise ValueError('reset_at_cycle must be in the execution window after initial reset')
    return c


def synthetic(config, seed):
    """Project-authored calibration patterns; seed does not depend on ISP controls."""
    c=validate(config); w,h=c['width'],c['height']; y,x=np.indices((h,w))
    images=[]
    for f in range(c['frames']):
        if c['source_pattern']=='gradient':
            value=(x*37+y*61+int(seed)%4096+f*257)%4096
        elif c['source_pattern']=='color_bars':
            colors=np.array([[4095,4095,4095],[4095,4095,0],[0,4095,4095],
                             [0,4095,0],[4095,0,4095],[4095,0,0],[0,0,4095],[0,0,0]])
            rgb=colors[np.minimum(x*8//w,7)]
            value=np.zeros((h,w),dtype=np.int64)
            for py in range(2):
                for px in range(2):
                    channel=PATTERNS[c['bayer_pattern']][py][px][0]
                    value[py::2,px::2]=rgb[py::2,px::2,'RGB'.index(channel)]
        else:
            value=np.zeros((h,w),dtype=np.int64)
            value[h//2-1:h//2+1,w//2-1:w//2+1]=4095
            value[2,2]=int(seed)%4096
        images.append(value.astype('<u2'))
    return np.stack(images)


def golden(raw, config):
    c=validate(config); raw=np.asarray(raw)
    if raw.shape != (c['frames'],c['height'],c['width']) or np.any(raw>4095):
        raise ValueError('RAW input shape or values do not match RAW12 configuration')
    blc=np.maximum(raw.astype(np.int64)-c['black_level'],0)
    wb=blc.copy()
    for py in range(2):
        for px in range(2):
            g=c['gains'][GAIN_INDEX[PATTERNS[c['bayer_pattern']][py][px]]]
            wb[:,py::2,px::2]=np.minimum((blc[:,py::2,px::2]*g)//128,4095)
    kernels=[
        np.array([[0,0,-2,0,0],[0,0,4,0,0],[-2,4,8,4,-2],[0,0,4,0,0],[0,0,-2,0,0]]),
        np.array([[0,0,1,0,0],[0,-2,0,-2,0],[-2,8,10,8,-2],[0,-2,0,-2,0],[0,0,1,0,0]]),
        np.array([[0,0,-3,0,0],[0,4,0,4,0],[-3,0,12,0,-3],[0,4,0,4,0],[0,0,-3,0,0]])]
    windows=np.lib.stride_tricks.sliding_window_view(wb,(5,5),axis=(-2,-1))
    def conv(k): return np.clip(np.einsum('fxyij,ij->fxy',windows,k)//16,0,4095)
    green,horizontal,cross=map(conv,kernels); vertical=conv(kernels[1].T)
    center=wb[:,2:-2,2:-2]; rgb=np.empty((*center.shape,3),dtype='<u2')
    for py in range(2):
        for px in range(2):
            color=PATTERNS[c['bayer_pattern']][py][px]
            sl=(slice(None),slice(py,None,2),slice(px,None,2))
            if color=='R': vals=(center,green,cross)
            elif color=='B': vals=(cross,green,center)
            elif color=='Gr': vals=(horizontal,center,vertical)
            else: vals=(vertical,center,horizontal)
            for i,v in enumerate(vals):rgb[sl+(i,)]=v[sl]
    return dict(blc=blc.astype('<u2'),wb=wb.astype('<u2'),rgb=rgb)
