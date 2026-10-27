"""Independent fixed wire vectors and adversarial decoder checks."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import struct

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src/axiom_vecu_network/wire.py"
spec = importlib.util.spec_from_file_location("network_wire_under_test", SOURCE)
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)
CONFIG = {"source_ip":"192.0.2.10", "destination_ip":"192.0.2.20",
          "source_port":24000, "destination_port":24001}
EMPTY = {"sequence":1,"time_s":.05,"objects":[]}
OBJECT = {"sequence":1,"time_s":.05,"objects":[
    {"id":"a","x":1.,"y":-2.,"z":0.,"heading_rad":.5,"speed_mps":3.}]}
# Ethernet addresses / IPv4 test-net addresses / UDP 24000→24001 / AXO1 fields.
# IPv4 lengths 0x2e/0x58; timestamp is integer 50,000,000ns; doubles are IEEE754 BE.
EMPTY_HEX = "02000000002002000000001008004500002e000140004011b69fc000020ac00002145dc05dc1001a3c1441584f31000000010000000002faf0800000"
OBJECT_HEX = "020000000020020000000010080045000058000140004011b675c000020ac00002145dc05dc10044ba8441584f31000000010000000002faf080000101613ff0000000000000c00000000000000000000000000000003fe00000000000004008000000000000"

def ones_complement(data):
    """Independent big integer sum, separate from production struct/unpack code."""
    value = 0
    for i in range(0,len(data),2):
        value += data[i]*256 + (data[i+1] if i+1<len(data) else 0)
        value = (value & 0xffff) + (value >> 16)
    return 0xffff-value

def with_payload(payload):
    frame=bytearray.fromhex(EMPTY_HEX)
    ip=frame[14:34];udp=bytearray(frame[34:42])+payload
    udp[4:6]=len(udp).to_bytes(2,"big");udp[6:8]=b"\0\0"
    pseudo=bytes(ip[12:20])+bytes([0,17])+len(udp).to_bytes(2,"big")
    udp[6:8]=(ones_complement(pseudo+udp) or 0xffff).to_bytes(2,"big")
    ip[2:4]=(20+len(udp)).to_bytes(2,"big");ip[10:12]=b"\0\0"
    ip[10:12]=ones_complement(ip).to_bytes(2,"big")
    result=bytes(frame[:14])+ip+udp
    return result + bytes(max(0,60-len(result)))

def test_rfc1071_checksum_vector():
    # RFC1071 section3 sample: 0001 f203 f4f5 f6f7 -> sum ddF2 -> complement 220D.
    value=bytes.fromhex("0001f203f4f5f6f7")
    assert wire.checksum(value)==ones_complement(value)==0x220d
    assert wire.checksum(value+b"\x80")==ones_complement(value+b"\x80")

@pytest.mark.parametrize("packet,encoded",[(EMPTY,EMPTY_HEX),(OBJECT,OBJECT_HEX)])
def test_fixed_golden_frame_and_independent_header_checks(packet,encoded):
    expected=bytes.fromhex(encoded)
    assert wire.ethernet(packet,CONFIG)==expected
    assert wire.decode_ethernet(expected,CONFIG)==packet
    assert ones_complement(expected[14:34])==0
    udp=expected[34:]
    assert ones_complement(expected[26:34]+b"\0\x11"+len(udp).to_bytes(2,"big")+udp)==0

@pytest.mark.parametrize("offset",[0,6,12,14,16,18,20,22,23,24,26,30,34,36,38,40,42,48,55,59])
def test_mutated_headers_and_payload_rejected(offset):
    frame=bytearray.fromhex(EMPTY_HEX);frame[offset]^=1
    with pytest.raises(ValueError):wire.decode_ethernet(frame,CONFIG)

@pytest.mark.parametrize("bad",[b"",bytes(59),bytes(1515)])
def test_frame_length_rejected(bad):
    with pytest.raises(ValueError):wire.decode_ethernet(bad,CONFIG)

def test_wrong_expected_endpoint_rejected():
    frame=bytes.fromhex(EMPTY_HEX)
    for field,value in [("source_ip","192.0.2.11"),("destination_ip","192.0.2.21"),
                        ("source_port",24002),("destination_port",24002)]:
        with pytest.raises(ValueError):wire.decode_ethernet(frame,dict(CONFIG,**{field:value}))

def test_checksum_zero_is_not_accepted_as_udp_checksum_disabled():
    frame=bytearray.fromhex(EMPTY_HEX);frame[40:42]=b"\0\0"
    with pytest.raises(ValueError):wire.decode_ethernet(frame,CONFIG)

@pytest.mark.parametrize("change",["bad_magic","too_many_objects","truncated_name","empty_name",
                                    "name_over64","invalid_utf8","nonfinite","trailing_byte","duplicate_id"])
def test_valid_checksums_do_not_hide_invalid_application_payload(change):
    payload=bytearray.fromhex(OBJECT_HEX)[42:]
    if change=="bad_magic":payload[0]=0
    elif change=="too_many_objects":payload[16:18]=(33).to_bytes(2,"big")
    elif change=="truncated_name":payload=payload[:19]
    elif change=="empty_name":payload[18]=0
    elif change=="name_over64":payload[18]=65
    elif change=="invalid_utf8":payload[19]=0xff
    elif change=="nonfinite":payload[20:28]=struct.pack("!d",float("nan"))
    elif change=="trailing_byte":payload+=b"\0"
    elif change=="duplicate_id":payload[16:18]=b"\0\2";payload+=payload[18:]
    frame=with_payload(payload)
    with pytest.raises(ValueError):wire.decode_ethernet(frame,CONFIG)

def test_nonzero_padding_rejected_but_zero_padding_accepted():
    frame=bytes.fromhex(EMPTY_HEX)
    assert wire.decode_ethernet(frame+b"\0"*4,CONFIG)==EMPTY
    with pytest.raises(ValueError):wire.decode_ethernet(frame+b"\0\1",CONFIG)

def test_fixed_can_golden_byte_order_scaling_sequence_and_epoch():
    value={"throttle":.5,"brake":.25,"steer":-1.}
    expected=bytes.fromhex("8813c40901800102")
    assert bytes(wire.command_bytes(value,257,2))==expected
    assert wire.decode_command(expected)==(value,1,2)

@pytest.mark.parametrize("data",[bytes(7),bytes(9),bytes.fromhex("1127000000000000"),
                                 bytes.fromhex("0000112700000000"),bytes.fromhex("0000000000800000")])
def test_can_invalid_dlc_and_out_of_range_signal_rejected(data):
    with pytest.raises(ValueError):wire.decode_command(data)

@pytest.mark.parametrize("value",[{"throttle":1.1,"brake":0.,"steer":0.},
                                    {"throttle":0.,"brake":-.1,"steer":0.},
                                    {"throttle":0.,"brake":0.,"steer":1.1},
                                    {"throttle":0.,"brake":float("nan"),"steer":0.}])
def test_can_encoder_rejects_nonfinite_and_range_errors(value):
    with pytest.raises(ValueError):wire.command_bytes(value,0,0)

def test_utf8_object_and_maximum_unfragmented_payload():
    packet=dict(EMPTY,objects=[dict(OBJECT["objects"][0],id="차량")])
    assert wire.decode_ethernet(wire.ethernet(packet,CONFIG),CONFIG)==packet
    oversized=dict(EMPTY,objects=[dict(OBJECT["objects"][0],id=str(i)+"x"*60) for i in range(32)])
    with pytest.raises(ValueError,match="MTU"):wire.ethernet(oversized,CONFIG)
