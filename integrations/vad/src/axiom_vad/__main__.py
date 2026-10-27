"""Native research connector. Output is published only after successful execution."""
import argparse
import json
from pathlib import Path

from ade2e.driving_contract import canonical, preflight
from .nuscenes_input import export_input, verify_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action',required=True)
    actions.add_parser('environment')
    export = actions.add_parser('prepare')
    export.add_argument('--data-root',type=Path,required=True)
    export.add_argument('--scene-token',required=True)
    export.add_argument('--sample-token',required=True)
    export.add_argument('--frames',type=int,default=3)
    export.add_argument('--route-command',choices=('all_modes','right','left','straight'),default='all_modes')
    export.add_argument('--rights-note',required=True)
    export.add_argument('--output',type=Path,required=True)
    check = actions.add_parser('check')
    check.add_argument('--input',type=Path,required=True)
    check.add_argument('--data-root',type=Path,required=True)
    run = actions.add_parser('run')
    run.add_argument('--source',type=Path,required=True)
    run.add_argument('--data-root',type=Path,required=True)
    run.add_argument('--checkpoint',type=Path,required=True)
    run.add_argument('--input',type=Path,required=True)
    run.add_argument('--output',type=Path,required=True)
    run.add_argument('--seed',type=int,default=42)
    run.add_argument('--weights-rights',required=True)
    run.add_argument('--confirm-trusted-native-code',action='store_true')
    args = parser.parse_args()
    if hasattr(args,'output') and args.output.exists():
        parser.error('Output already exists; preserve previous evidence')
    if args.action == 'environment':
        from .native import inspect_environment
        result = inspect_environment()
    elif args.action == 'prepare':
        result = export_input(args.data_root,args.scene_token,args.sample_token,frame_count=args.frames,
            route_command=args.route_command,rights_note=args.rights_note)
    elif args.action == 'check':
        value = json.loads(args.input.read_text())
        verify_files(args.data_root,value)
        result = {**preflight(value),'file_identity_status':'verified_at_native_data_root'}
    else:
        if not args.confirm_trusted_native_code or not 0 <= args.seed <= 2**31-1:
            parser.error('Confirm trusted source/checkpoint code and a valid seed')
        from .native import predict
        result = predict(args.source,args.data_root,args.checkpoint,json.loads(args.input.read_text()),
            seed=args.seed,weights_rights=args.weights_rights)
    if hasattr(args,'output'):
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('xb') as target:
            target.write(canonical(result))
        print(json.dumps({'status':'saved','path':str(args.output),'schema_version':result['schema_version']}))
    else:
        print(json.dumps(result))


if __name__ == '__main__':
    main()
