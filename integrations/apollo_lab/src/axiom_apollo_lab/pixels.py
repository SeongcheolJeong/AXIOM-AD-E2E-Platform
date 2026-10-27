"""Deterministic CPU reference ISP. These are not Qualcomm algorithms."""
import hashlib
import numpy as np
import cv2
from ade2e.apollo_lab_schemas import RawMetadata

BAYER={'RGGB':((0,1),(1,2)),'BGGR':((2,1),(1,0)),'GRBG':((1,0),(2,1)),'GBRG':((1,2),(0,1))}

def decode(data, metadata, index=0):
    m=RawMetadata.model_validate(metadata); start=index*m.frame_bytes
    raw=np.frombuffer(data,dtype=np.uint8,count=m.frame_bytes,offset=start).reshape(m.height,m.stride_bytes or m.row_bytes)[:,:m.row_bytes].copy()
    if m.packing=='uint16le':out=raw.reshape(-1).view('<u2').reshape(m.height,m.width)
    elif m.bits==12:
        a=raw.reshape(m.height,-1,3).astype(np.uint16);out=np.empty((m.height,m.width),np.uint16)
        out[:,0::2]=(a[:,:,0]<<4)|(a[:,:,2]&15);out[:,1::2]=(a[:,:,1]<<4)|(a[:,:,2]>>4)
    else:
        a=raw.reshape(m.height,-1,5).astype(np.uint16);out=np.empty((m.height,m.width),np.uint16)
        for i in range(4):out[:,i::4]=(a[:,:,i]<<2)|((a[:,:,4]>>(2*i))&3)
    if np.any(out>=2**m.bits):raise ValueError('RAW uint16 sample exceeds declared sensor bit depth')
    return out

def synthetic(metadata, channel, frame, seed):
    m=RawMetadata.model_validate(metadata);h,w=m.height,m.width
    yy,xx=np.mgrid[:h,:w]; x=xx/w; y=yy/h
    colors=np.array([[.9,.1,.1],[.1,.9,.1],[.1,.1,.9],[.7,.7,.7],[.1,.1,.1],[.9,.8,.15]],np.float32)
    rgb=colors[np.minimum((x*6).astype(int),5)]*(.3+.7*y[:,:,None])
    bar=((xx-frame*max(1,w//30)-channel*w//8)%w)<max(4,w//12)
    rgb[bar]=.92
    raw=np.empty((h,w),np.float32)
    for r in range(2):
        for c in range(2):raw[r::2,c::2]=rgb[r::2,c::2,BAYER[m.bayer][r][c]]
    rng=np.random.default_rng(np.random.SeedSequence([seed,channel,frame]))
    raw=np.clip(raw+rng.normal(0,.008,raw.shape),0,1)
    return np.rint(raw*(m.white_level-m.black_level)+m.black_level).astype(np.uint16)

def image_hash(array):return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()

def rgb_to_yuv(rgb):
    rgb=rgb.astype(np.float32)/255
    y=rgb@np.array([.2126,.7152,.0722],np.float32)
    u=(rgb[:,:,2]-y)/1.8556+.5;v=(rgb[:,:,0]-y)/1.5748+.5
    return np.rint(np.clip(np.stack([y,u,v],axis=-1),0,1)*255).astype(np.uint8)

def yuv_to_rgb(yuv):
    a=yuv.astype(np.float32)/255;y=a[:,:,0];u=a[:,:,1]-.5;v=a[:,:,2]-.5
    r=y+1.5748*v;b=y+1.8556*u;g=(y-.2126*r-.0722*b)/.7152
    return np.rint(np.clip(np.stack([r,g,b],axis=-1),0,1)*255).astype(np.uint8)

def process(raw, metadata, p):
    m=RawMetadata.model_validate(metadata)
    a=np.clip((raw.astype(np.float32)-m.black_level)/(m.white_level-m.black_level),0,1)
    gray=np.rint(a*255).astype(np.uint8);raw_display=np.repeat(gray[:,:,None],3,axis=2)
    yy,xx=np.mgrid[:m.height,:m.width];shade=1+p['shading']*((xx/m.width-.5)**2+(yy/m.height-.5)**2)*2
    for r in range(2):
        for c in range(2):a[r::2,c::2]*=p['wb'][BAYER[m.bayer][r][c]]*shade[r::2,c::2]
    tfe=np.rint(np.clip(a,0,1)*65535).astype(np.uint16)
    # Explicit four-letter CFA aliases avoid the historical two-letter OpenCV naming ambiguity.
    code=getattr(cv2,'COLOR_Bayer'+m.bayer+'2RGB')
    linear=cv2.cvtColor(tfe,code).astype(np.float32)/65535
    ofe_rgb=np.rint(np.clip(linear*p['exposure'],0,1)**(1/p['gamma'])*255).astype(np.uint8)
    yuv=rgb_to_yuv(ofe_rgb)
    blur=cv2.GaussianBlur(yuv,(3,3),0).astype(np.float32)
    den=yuv.astype(np.float32)*(1-p['denoise'])+blur*p['denoise']
    den[:,:,0]+=p['sharpen']*(den[:,:,0]-cv2.GaussianBlur(den[:,:,0],(3,3),0))
    ipe_yuv=np.rint(np.clip(den,0,255)).astype(np.uint8)
    tfe_rgb=cv2.cvtColor(tfe,code);tfe_display=(tfe_rgb/257).astype(np.uint8)
    return [raw_display,tfe_display,yuv_to_rgb(yuv),yuv_to_rgb(ipe_yuv)],dict(raw_sha256=image_hash(raw),tfe_sha256=image_hash(tfe),ofe_yuv_sha256=image_hash(yuv),ipe_yuv_sha256=image_hash(ipe_yuv),
        format='YUV444 uint8 BT.709 full-range reference',histogram=np.histogram(raw,bins=32,range=(0,2**m.bits))[0].tolist(),mean_raw=float(raw.mean()),clipped_pixels=int((raw>=m.white_level).sum()))
