"""Linux SocketCAN transport shared by vcan and physical CAN, with no emulation fallback."""
import socket,struct,time

FRAME=struct.Struct('=IB3x8s')

def encode_frame(can_id,data):
    if type(can_id) is not int or not 0<=can_id<=0x7ff or len(data)!=8:raise ValueError('Classic 11-bit CAN with 8 data bytes required')
    return FRAME.pack(can_id,8,bytes(data))

def decode_frame(raw):
    if len(raw)!=FRAME.size:raise ValueError('Incomplete CAN frame')
    can_id,dlc,data=FRAME.unpack(raw)
    if can_id>0x7ff or dlc!=8:raise ValueError('Unsupported CAN flags/identifier/DLC')
    return can_id,list(data)

class CanBus:
    def __init__(self,interface):
        if not hasattr(socket,'AF_CAN'):raise OSError('Linux SocketCAN is required; no software fallback')
        self.socket=socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW)
        try:
            self.socket.setsockopt(socket.SOL_CAN_RAW,2,struct.pack('=I',0x1fffffff))
            self.socket.setsockopt(socket.SOL_SOCKET,40,1) # SO_RXQ_OVFL
            self.socket.setsockopt(socket.SOL_SOCKET,35,1) # SO_TIMESTAMPNS, kernel realtime clock
            self.socket.bind((interface,));self.socket.setblocking(False)
        except BaseException:self.socket.close();raise
        self.interface=interface
    def send(self,can_id,data):
        if self.socket.send(encode_frame(can_id,data))!=FRAME.size:raise OSError('Incomplete CAN submission')
    def receive(self):
        try:raw,anc,flags,_=self.socket.recvmsg(72,128)
        except BlockingIOError:return None
        received=time.monotonic_ns();kernel=None
        for level,kind,value in anc:
            if level==socket.SOL_SOCKET and kind==40 and struct.unpack('=I',value[:4])[0]:
                raise OSError('SocketCAN receive queue overflow')
            if level==socket.SOL_SOCKET and kind==35:
                sec,ns=struct.unpack('@ll',value[:struct.calcsize('@ll')]);kernel=sec*10**9+ns
        raw_id=struct.unpack('=I',raw[:4])[0]
        if raw_id&0x20000000:
            return dict(frame_type='error',error_mask=raw_id&0x1fffffff,raw_hex=raw.hex(),observed_monotonic_ns=received,
                kernel_realtime_ns=kernel,local_echo=False,interface=self.interface)
        cid,data=decode_frame(raw)
        return dict(can_id=cid,data=data,data_hex=bytes(data).hex(),observed_monotonic_ns=received,
            kernel_realtime_ns=kernel,local_echo=bool(flags&socket.MSG_DONTROUTE),timestamp_source='kernel_software_rx',interface=self.interface)
    def close(self):self.socket.close()

def setup_vcan(names=('axiomha','axiomhb')):
    """Explicit operator setup, intended for a dedicated network namespace only."""
    def attr(k,v):
        b=struct.pack('HH',len(v)+4,k)+v
        return b+b'\0'*((-len(b))%4)
    def request(kind,flags,payload,seq):
        with socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,0) as s:
            s.bind((0,0));s.send(struct.pack('IHHII',16+len(payload),kind,flags,seq,0)+payload)
            data=s.recv(4096);error=struct.unpack('i',data[16:20])[0]
            if error:raise OSError(-error,'netlink setup failed')
    for index,name in enumerate(names):
        if name not in dict((n,i) for i,n in socket.if_nameindex()):
            p=struct.pack('BBHiII',0,0,0,0,0,0)+attr(3,name.encode()+b'\0')+attr(18,attr(1,b'vcan\0'))
            request(16,0x605,p,index+1)
        request(16,5,struct.pack('BBHiII',0,0,0,socket.if_nametoindex(name),1,1),index+10)
    request(16,5,struct.pack('BBHiII',0,0,0,socket.if_nametoindex('lo'),1,1),20)
