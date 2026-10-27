"""Actual sensor bytes and independent ROS2 writer fixtures, not simulator runs."""

import base64
import hashlib
import io
import json
import math
import struct
import zlib

import pytest
from PIL import Image
from mcap.reader import make_reader
from mcap_ros2._cdr import CdrWriter, EncapsulationKind
from mcap_ros2.writer import Writer

from ade2e.sensor_decoders import MAX_INPUT_BYTES, SensorDecodeError, SensorDecoderRegistry, validate_png_preview


HEADER = """\n================================================================================
MSG: std_msgs/Header
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
"""
IMAGE = """std_msgs/Header header
uint32 height
uint32 width
string encoding
uint8 is_bigendian
uint32 step
uint8[] data
""" + HEADER
COMPRESSED = """std_msgs/Header header
string format
uint8[] data
""" + HEADER
CLOUD = """std_msgs/Header header
uint32 height
uint32 width
sensor_msgs/PointField[] fields
bool is_bigendian
uint32 point_step
uint32 row_step
uint8[] data
bool is_dense
""" + HEADER + """================================================================================
MSG: sensor_msgs/PointField
uint8 INT8=1
uint8 UINT8=2
uint8 INT16=3
uint8 UINT16=4
uint8 INT32=5
uint8 UINT32=6
uint8 FLOAT32=7
uint8 FLOAT64=8
string name
uint32 offset
uint8 datatype
uint32 count
"""


def descriptor(name, definition, *, ros=False):
    raw = definition.encode()
    schema = dict(id=1, name=name, encoding="ros2msg" if ros else "jsonschema", definition=definition, definition_truncated=False, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    return dict(id=1,schema_id=1,message_encoding="cdr" if ros else "json"),schema


def fox(name, message):
    types = {"timestamp":"object", "frame_id":"string", "data":"string", "format":"string", "width":"integer", "height":"integer", "encoding":"string", "step":"integer", "pose":"object", "point_stride":"integer", "fields":"array"}
    props = {key:{"type":types[key]} for key in message if key in types}
    props["data"]["contentEncoding"] = "base64"
    channel,schema = descriptor(name,json.dumps(dict(type="object",title=name,properties=props)))
    return json.dumps(message).encode(),channel,schema


def ros_message(name, definition, message):
    """Use upstream's public MCAP writer, then extract its real CDR bytes."""
    output = io.BytesIO()
    writer = Writer(output)
    registered = writer.register_msgdef(name,definition)
    writer.write_message("/sensor",registered,message,log_time=0,publish_time=0)
    writer.finish()
    _,_,entry = next(make_reader(io.BytesIO(output.getvalue())).iter_messages())
    channel,schema = descriptor(name,definition,ros=True)
    return entry.data,channel,schema


def decode(fixture, **kwargs):
    payload,channel,schema = fixture
    adapter = SensorDecoderRegistry().get("ros2-cdr" if channel["message_encoding"]=="cdr" else "foxglove-json","1")
    return adapter.decode(payload,channel,schema,**kwargs)


def stamp():
    return dict(timestamp=dict(sec=17,nsec=123456789),frame_id="sensor")


def image_bytes(fmt="PNG", size=(2,1)):
    image = Image.new("RGB",size,(200,30,5))
    output = io.BytesIO()
    image.save(output,format=fmt)
    return output.getvalue()


def png_pixels(result):
    raw = base64.b64decode(result.camera.data_base64)
    assert result.camera.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.camera.size_bytes == len(raw)
    image = Image.open(io.BytesIO(raw))
    assert image.format == "PNG"
    return list(image.get_flattened_data())


def cloud_message(points, *, pose=None, intensity=True):
    pose = pose or dict(position=dict(x=0,y=0,z=0),orientation=dict(x=0,y=0,z=0,w=1))
    axes = "xyzi" if intensity else "xyz"
    names = ["x","y","z","intensity"] if intensity else ["x","y","z"]
    return dict(**stamp(),pose=pose,point_stride=4*len(axes),fields=[dict(name=name,offset=offset*4,type=7) for offset,name in enumerate(names)],data=base64.b64encode(b"".join(struct.pack("<"+"f"*len(axes),*point) for point in points)).decode())


def unpack_points(result):
    cloud = result.point_cloud
    raw = base64.b64decode(cloud.data_base64)
    assert cloud.sha256 == hashlib.sha256(raw).hexdigest()
    assert cloud.size_bytes == cloud.point_count * cloud.point_stride == len(raw)
    return list(struct.iter_unpack("<fff",raw))


@pytest.mark.parametrize("format_name",["png","jpeg"])
def test_fox_compressed_actual_pixels(format_name):
    payload = image_bytes(format_name.upper())
    fixture = fox("foxglove.CompressedImage",dict(**stamp(),format=format_name,data=base64.b64encode(payload).decode()))
    result = decode(fixture)
    assert result.capture_time_ns == "17123456789"
    assert result.frame_id == "sensor"
    assert result.source_sha256 == hashlib.sha256(fixture[0]).hexdigest()
    assert result.source_size_bytes == len(fixture[0])
    for pixel in png_pixels(result):
        assert pixel == pytest.approx((200,30,5),abs=2)


@pytest.mark.parametrize("encoding,data,step,expected",[("bgr8",b"\x03\x02\x01xx\x06\x05\x04yy",5,[(1,2,3),(4,5,6)]),("bgra8",b"\x03\x02\x01\x80\x06\x05\x04\0",4,[(1,2,3),(4,5,6)]),("mono8",b"\x17\x2b",1,[(23,23,23),(43,43,43)])])
def test_fox_raw_row_padding_and_color_order(encoding,data,step,expected):
    result = decode(fox("foxglove.RawImage",dict(**stamp(),width=1,height=2,encoding=encoding,step=step,data=base64.b64encode(data).decode())))
    assert png_pixels(result)==expected


def test_fox_pose_applied_without_rig_or_handedness_inference():
    half = math.sqrt(0.5)
    pose = dict(position=dict(x=10,y=20,z=30),orientation=dict(x=0,y=0,z=half,w=half))
    result = decode(fox("foxglove.PointCloud",cloud_message([(1,0,2,42)],pose=pose)))
    assert unpack_points(result)==pytest.approx([(10,21,32)])
    assert result.transform_policy=="message_pose_to_frame"
    assert result.coordinate_convention=="source_declared"
    assert result.source_pose==pose
    assert struct.unpack("<f",base64.b64decode(result.point_cloud.intensity.data_base64))==(42,)


def test_cloud_nan_drop_and_deterministic_stride_preserve_denominator():
    result = decode(fox("foxglove.PointCloud",cloud_message([(i,0,0,1) if i!=3 else (float("nan"),0,0,1) for i in range(10)])),max_points=3)
    assert unpack_points(result)==[(0,0,0),(4,0,0),(8,0,0)]
    cloud=result.point_cloud
    assert (cloud.original_point_count,cloud.invalid_points,cloud.skipped_points,cloud.sampling_stride)==(10,1,6,4)
    assert cloud.point_count+cloud.invalid_points+cloud.skipped_points==10
    assert cloud.bounds_min==(0,0,0) and cloud.bounds_max==(8,0,0)


def test_nonfinite_intensity_is_not_fabricated():
    result=decode(fox("foxglove.PointCloud",cloud_message([(1,2,3,float("nan")),(4,5,6,2)])))
    assert len(unpack_points(result))==2
    assert result.point_cloud.intensity is None
    assert result.point_cloud.intensity_invalid_points==1


def test_empty_point_cloud():
    result=decode(fox("foxglove.PointCloud",cloud_message([])))
    assert unpack_points(result)==[]
    assert result.point_cloud.bounds_min is None


def test_ros2_image_public_writer_real_cdr():
    fixture=ros_message("sensor_msgs/msg/Image",IMAGE,dict(header=dict(stamp=dict(sec=-1,nanosec=999999999),frame_id="camera_optical"),height=1,width=2,encoding="rgb8",is_bigendian=0,step=6,data=b"\xff\0\0\0\xff\0"))
    result=decode(fixture)
    assert png_pixels(result)==[(255,0,0),(0,255,0)]
    assert result.capture_time_ns=="-1"
    assert result.frame_id=="camera_optical"


def test_ros_generic_8uc3_does_not_infer_color_but_foxglove_alias_is_bgr():
    message=dict(header=dict(stamp=dict(sec=1,nanosec=0),frame_id="camera"),height=1,width=1,encoding="8UC3",is_bigendian=0,step=3,data=b"\x03\x02\x01")
    with pytest.raises(SensorDecodeError) as error:
        decode(ros_message("sensor_msgs/msg/Image",IMAGE,message))
    assert error.value.code=="unsupported_schema"
    result=decode(fox("foxglove.RawImage",dict(**stamp(),height=1,width=1,encoding="8UC3",step=3,data="AwIB")))
    assert png_pixels(result)==[(1,2,3)]


@pytest.mark.parametrize("fmt",["png","rgb8; png compressed rgb8","bgr8; jpeg compressed bgr8"])
def test_ros2_compressed_image_public_writer(fmt):
    data=image_bytes("JPEG" if "jpeg" in fmt else "PNG")
    result=decode(ros_message("sensor_msgs/msg/CompressedImage",COMPRESSED,dict(header=dict(stamp=dict(sec=1,nanosec=7),frame_id="camera"),format=fmt,data=data)))
    assert len(png_pixels(result))==2
    assert result.capture_time_ns=="1000000007"


@pytest.mark.parametrize("bigendian",[False,True])
def test_ros_cloud_row_padding_and_independent_binary_endianness(bigendian):
    fields=[dict(name=name,offset=i*4,datatype=7,count=1) for i,name in enumerate(("x","y","z","intensity"))]
    endian=">" if bigendian else "<"
    payload=b"".join(struct.pack(endian+"ffff",*point)+b"padding!" for point in [(1,2,3,4),(-4,5,-6,7)])
    message=dict(header=dict(stamp=dict(sec=42,nanosec=3),frame_id="camera_optical"),height=2,width=1,fields=fields,is_bigendian=bigendian,point_step=16,row_step=24,data=payload,is_dense=True)
    result=decode(ros_message("sensor_msgs/msg/PointCloud2",CLOUD,message))
    assert unpack_points(result)==[(1,2,3),(-4,5,-6)]
    assert result.coordinate_convention=="source_declared"


def test_big_endian_cdr1_uses_upstream_cdr_writer():
    output=io.BytesIO()
    writer=CdrWriter(output,EncapsulationKind.CDR_BE)
    writer.write_int32(9); writer.write_uint32(17); writer.write_string("camera")
    writer.write_uint32(1); writer.write_uint32(1); writer.write_string("bgr8")
    writer.write_uint8(1); writer.write_uint32(3); writer.write_uint32(3); writer.write_bytes(b"\x03\x02\x01")
    channel,schema=descriptor("sensor_msgs/msg/Image",IMAGE,ros=True)
    result=decode((output.getvalue(),channel,schema))
    assert png_pixels(result)==[(1,2,3)]
    assert result.capture_time_ns=="9000000017"


@pytest.mark.parametrize("change",[lambda message:message.update(step=2),lambda message:message.update(height=2_000_001),lambda message:message.update(encoding="bayer_rggb8"),lambda message:message.update(timestamp=dict(sec=0,nsec=1_000_000_000)),lambda message:message.update(data="%%%")])
def test_invalid_raw_fail_closed(change):
    message=dict(**stamp(),width=1,height=1,encoding="rgb8",step=3,data="AAAA")
    change(message)
    with pytest.raises(SensorDecodeError): decode(fox("foxglove.RawImage",message))


@pytest.mark.parametrize("change",[lambda message:message["fields"][0].update(offset=999),lambda message:message["fields"][0].update(type=1),lambda message:message["fields"].append(message["fields"][0]),lambda message:message.update(point_stride=15),lambda message:message["pose"]["orientation"].update(w=0)])
def test_invalid_point_layout_fail_closed(change):
    message=cloud_message([(1,2,3,4)])
    change(message)
    with pytest.raises(SensorDecodeError):decode(fox("foxglove.PointCloud",message))


def test_schema_mutation_and_nonstandard_cdr_are_not_accepted():
    channel,schema=descriptor("sensor_msgs/msg/Image",IMAGE,ros=True)
    adapter=SensorDecoderRegistry().get("ros2-cdr")
    assert adapter.accepts(channel,schema)
    assert adapter.kind_for(channel,schema)=="camera"
    for change in [dict(definition_truncated=True),dict(sha256="a"*64),dict(encoding="ros2idl"),dict(name="sensor_msgs/msg/CameraInfo")]:
        assert not adapter.accepts(channel,{**schema,**change})
    for definition in [IMAGE.replace("uint32 width","uint64 width"),IMAGE+"float64 injected\n",IMAGE.split("===")[0],IMAGE.replace("int32 sec","int64 sec")]:
        badchannel,badschema=descriptor("sensor_msgs/msg/Image",definition,ros=True)
        assert not adapter.accepts(badchannel,badschema)


def test_cdr_length_bomb_truncation_and_other_encapsulation_rejected():
    channel,schema=descriptor("sensor_msgs/msg/CompressedImage",COMPRESSED,ros=True)
    # Header frame string length is rejected before decoding or allocating data.
    for payload in [b"\0\1\0\0"+struct.pack("<iII",1,2,0xffffffff), b"\0\7\0\0"+b"\0"*100, b"\0\1\0\0"]:
        with pytest.raises(SensorDecodeError):decode((payload,channel,schema))
    fixture=ros_message("sensor_msgs/msg/CompressedImage",COMPRESSED,dict(header=dict(stamp=dict(sec=0,nanosec=0),frame_id="camera"),format="png",data=image_bytes()))
    with pytest.raises(SensorDecodeError):decode((fixture[0][:-1],*fixture[1:]))
    with pytest.raises(SensorDecodeError):decode((fixture[0]+b"junk",*fixture[1:]))


def test_input_nesting_and_json_structure_limits():
    fixture=fox("foxglove.CompressedImage",dict(**stamp(),format="png",data=""))
    for data in [b" "*(MAX_INPUT_BYTES+1),b'{"x":'+b"["*17+b"0"+b"]"*17+b"}",b'{"x":['+b"0,"*5000+b"0]}"]:
        with pytest.raises(SensorDecodeError) as error:decode((data,*fixture[1:]))
        assert error.value.code=="limit_exceeded"


def test_png_bomb_dimensions_crc_trailing_data_and_wrong_declared_codec():
    raw=image_bytes()
    invalid=bytearray(raw); invalid[20:24]=struct.pack(">I",2_000_001); invalid[29:33]=struct.pack(">I",zlib.crc32(invalid[12:29]))
    for data,format_name in [(bytes(invalid),"png"),(raw[:-1],"png"),(raw+b"trailing","png"),(raw,"jpeg")]:
        with pytest.raises(SensorDecodeError):decode(fox("foxglove.CompressedImage",dict(**stamp(),format=format_name,data=base64.b64encode(data).decode())))


def test_png_text_metadata_is_never_inflated():
    raw=image_bytes()
    body=b"label\0\0"+zlib.compress(b"x"*2_000_000)
    chunk=struct.pack(">I",len(body))+b"zTXt"+body+struct.pack(">I",zlib.crc32(b"zTXt"+body))
    result=decode(fox("foxglove.CompressedImage",dict(**stamp(),format="png",data=base64.b64encode(raw[:33]+chunk+raw[33:]).decode())))
    assert png_pixels(result)==[(200,30,5)]*2


def test_public_png_preview_validator_decodes_without_reencoding(monkeypatch):
    def fail_save(*args,**kwargs):raise AssertionError("Validation must not re-encode")
    valid=image_bytes()
    monkeypatch.setattr(Image.Image,"save",fail_save)
    assert validate_png_preview(valid,2,1) is None
    for value,width,height in [(valid[:24],2,1),(valid,3,1),(valid+b"extra",2,1),(valid[:-1],2,1)]:
        with pytest.raises(SensorDecodeError):validate_png_preview(value,width,height)


def test_public_png_preview_validator_rejects_non_rgb8():
    output=io.BytesIO()
    Image.new("RGBA",(1,1),(1,2,3,255)).save(output,format="PNG")
    with pytest.raises(SensorDecodeError):validate_png_preview(output.getvalue(),1,1)


def test_registry_version_pins_and_replacement():
    registry=SensorDecoderRegistry()
    adapter=registry.get("foxglove-json")
    with pytest.raises(ValueError):registry.register(adapter)
    with pytest.raises(KeyError):registry.get("foxglove-json","2")
    class Replacement:
        def metadata(self):return adapter.metadata().model_copy(update={"version":"2"})
    replacement=Replacement()
    registry.register(replacement,replace=True)
    assert registry.get("foxglove-json","2") is replacement
    with pytest.raises(KeyError):registry.get("foxglove-json","1")
