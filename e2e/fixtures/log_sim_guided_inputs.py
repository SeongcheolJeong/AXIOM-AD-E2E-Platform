"""Authored MCAP inputs for the browser recovery check; no measured data."""
from pathlib import Path
import argparse
from mcap.writer import Writer
from ade2e.scene_sequences import EGO_SCHEMA, OBJECT_SCHEMA
from ade2e.log_sim_beta_packs import example_mcap
from ade2e.service import canonical_bytes


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    (root/'authored-missing-objects.mcap').write_bytes(example_mcap(True))
    with (root/'authored-no-candidates.mcap').open('wb') as out:
        writer=Writer(out);writer.start(profile='axiom-authored-beta-zero-candidate/1')
        ego_schema=writer.register_schema('axiom.PlanningEgo.v1','jsonschema',canonical_bytes(EGO_SCHEMA))
        object_schema=writer.register_schema('axiom.ObjectTracks.v1','jsonschema',canonical_bytes(OBJECT_SCHEMA))
        ego_channel=writer.register_channel('/planning/ego','json',ego_schema)
        object_channel=writer.register_channel('/planning/objects','json',object_schema)
        writer.add_metadata('axiom.authored_example',{'scope':'software input fixture; not measured road data'})
        for i in range(12):
            at=1_000_000_000+i*500_000_000
            ego=dict(timestamp_ns=str(at),frame_id='map',x_m=float(i)*2,y_m=0.,yaw_rad=0.,speed_mps=4.,length_m=4.5,width_m=1.9,source='authored_zero_candidate_example')
            objects=dict(timestamp_ns=str(at),frame_id='map',coordinate_frame='world',velocity_semantics='absolute_ground_velocity',source='authored_zero_candidate_example',objects=[])
            writer.add_message(ego_channel,at,canonical_bytes(ego),at,i)
            writer.add_message(object_channel,at,canonical_bytes(objects),at,i)
        writer.finish()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('runtime',type=Path)
    prepare(parser.parse_args().runtime)
