"""Authored MCAP for the recorded-input browser contract; no measured road claim."""
import json
from pathlib import Path
import sys
from mcap.writer import CompressionType, Writer
from ade2e.scene_sequences import EGO_SCHEMA, EGO_SCHEMA_NAME, OBJECT_SCHEMA, OBJECT_SCHEMA_NAME

with Path(sys.argv[1]).open('wb') as output:
    writer=Writer(output,compression=CompressionType.NONE);writer.start()
    es=writer.register_schema(EGO_SCHEMA_NAME,'jsonschema',json.dumps(EGO_SCHEMA).encode())
    os=writer.register_schema(OBJECT_SCHEMA_NAME,'jsonschema',json.dumps(OBJECT_SCHEMA).encode())
    ego_channel=writer.register_channel('/ego-world','json',es)
    object_channel=writer.register_channel('/object-world','json',os)
    for index in range(6):
        at=1_000_000_000+index*100_000_000
        ego={'timestamp_ns':str(at),'frame_id':'map','x_m':float(index)*.8,'y_m':0.,'yaw_rad':0.,
             'speed_mps':8.,'length_m':4.5,'width_m':1.9,'source':'Authored browser fixture with declared footprint'}
        objects={'timestamp_ns':str(at),'frame_id':'map','coordinate_frame':'world',
                 'velocity_semantics':'absolute_ground_velocity','source':'Authored object fixture',
                 'objects':[{'id':'lead','x_m':20.,'y_m':0.,'yaw_rad':0.,'length_m':4.5,'width_m':1.9,'vx_mps':0.,'vy_mps':0.}]}
        writer.add_message(object_channel,at,json.dumps(objects).encode(),at)
        writer.add_message(ego_channel,at,json.dumps(ego).encode(),at)
    writer.finish()
