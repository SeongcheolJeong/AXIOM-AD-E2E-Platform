"""Bounded reference payloads over real Ethernet/IPv4/UDP and Classic CAN bytes.

AXO1 is an AXIOM example application payload, not SOME/IP or an OEM protocol.
"""
import ipaddress,math,struct

def checksum(data):
    if len(data)%2:data+=b'\0'
    value=sum(struct.unpack('!%dH'%(len(data)//2),data))
    while value>>16:value=(value&65535)+(value>>16)
    return (~value)&65535

def object_payload(packet):
    objects=packet['objects']
    if len(objects)>32:raise ValueError('At most 32 objects')
    out=bytearray(struct.pack('!4sIQH',b'AXO1',packet['sequence'],round(packet['time_s']*1e9),len(objects)))
    for obj in objects:
        name=obj['id'].encode('utf8')
        if not 0<len(name)<=64:raise ValueError('Object identity exceeds wire contract')
        values=[obj.get(k,0.) for k in ('x','y','z','heading_rad','speed_mps')]
        if not all(math.isfinite(v) for v in values):raise ValueError('Nonfinite wire value')
        out+=bytes([len(name)])+name+struct.pack('!5d',*values)
    if len(out)>1472:raise ValueError('Object payload exceeds unfragmented UDP MTU')
    return bytes(out)

def parse_objects(data):
    if len(data)<18:raise ValueError('Truncated object packet')
    magic,seq,ns,count=struct.unpack('!4sIQH',data[:18]);offset=18;objects=[]
    if magic!=b'AXO1' or count>32:raise ValueError('Unknown object packet')
    for _ in range(count):
        if offset>=len(data):raise ValueError('Truncated object identity')
        length=data[offset];offset+=1
        if not length or length>64 or offset+length+40>len(data):raise ValueError('Truncated object fields')
        name=data[offset:offset+length].decode('utf8');offset+=length
        values=struct.unpack('!5d',data[offset:offset+40]);offset+=40
        if not all(math.isfinite(v) for v in values):raise ValueError('Nonfinite object')
        objects.append(dict(id=name,**dict(zip(('x','y','z','heading_rad','speed_mps'),values))))
    if offset!=len(data) or len({o['id'] for o in objects})!=count:raise ValueError('Object packet closure/identity mismatch')
    return dict(sequence=seq,time_s=ns/1e9,objects=objects)

def ethernet(packet,config):
    payload=object_payload(packet);src=ipaddress.IPv4Address(config['source_ip']).packed;dst=ipaddress.IPv4Address(config['destination_ip']).packed
    udp=struct.pack('!HHHH',config['source_port'],config['destination_port'],8+len(payload),0)+payload
    c=checksum(src+dst+struct.pack('!BBH',0,17,len(udp))+udp) or 65535
    udp=udp[:6]+struct.pack('!H',c)+udp[8:]
    ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(udp),packet['sequence']%65536,0x4000,64,17,0,src,dst)
    ip=ip[:10]+struct.pack('!H',checksum(ip))+ip[12:]
    frame=bytes.fromhex('0200000000200200000000100800')+ip+udp
    return frame+b'\0'*max(0,60-len(frame))

def decode_ethernet(frame,config):
    frame=bytes(frame)
    if len(frame)<60 or len(frame)>1514 or frame[:14]!=bytes.fromhex('0200000000200200000000100800'):raise ValueError('Ethernet identity/length mismatch')
    ip=frame[14:34]
    version,tos,total,ident,frag,ttl,protocol,c,src,dst=struct.unpack('!BBHHHBBH4s4s',ip)
    if version!=0x45 or protocol!=17 or frag!=0x4000 or checksum(ip)!=0 or total<28 or total+14>len(frame):raise ValueError('Invalid IPv4 header/fragment/checksum')
    if src!=ipaddress.IPv4Address(config['source_ip']).packed or dst!=ipaddress.IPv4Address(config['destination_ip']).packed:raise ValueError('Unexpected IPv4 endpoints')
    udp=frame[34:14+total];sp,dp,length,uc=struct.unpack('!HHHH',udp[:8])
    if (sp,dp)!=(config['source_port'],config['destination_port']) or length!=len(udp) or uc==0 or checksum(src+dst+struct.pack('!BBH',0,17,length)+udp)!=0:raise ValueError('Invalid UDP endpoint/length/checksum')
    if any(frame[14+total:]):raise ValueError('Nonzero Ethernet padding')
    return parse_objects(udp[8:])

def command_bytes(control,sequence,epoch):
    t,b,s=(control[k] for k in ('throttle','brake','steer'))
    if not all(math.isfinite(v) for v in (t,b,s)) or not 0<=t<=1 or not 0<=b<=1 or not -1<=s<=1:raise ValueError('Control bounds')
    return list(struct.pack('<HHhBB',round(t*10000),round(b*10000),round(s*32767),sequence%256,epoch%256))

def decode_command(data):
    if len(data)!=8:raise ValueError('CAN DLC must be 8')
    t,b,s,seq,epoch=struct.unpack('<HHhBB',bytes(data))
    if t>10000 or b>10000 or s==-32768:raise ValueError('CAN signal range')
    return {'throttle':t/10000,'brake':b/10000,'steer':s/32767},seq,epoch
