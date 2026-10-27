"""Explicit inspection and retrieval, with no automatic simulator retry."""
from .dataset_cli import _written

COMMANDS = {'inspect-remote-execution', 'submit-execution-recovery', 'execution-recoveries', 'execution-recovery',
            'wait-execution-recovery', 'cancel-execution-recovery', 'export-execution-recovery', 'export-remote-execution-archive'}


def register_recovery_commands(sub):
    sub.add_parser('inspect-remote-execution').add_argument('job_id')
    submit = sub.add_parser('submit-execution-recovery')
    submit.add_argument('source_job_id')
    submit.add_argument('--note', required=True)
    submit.add_argument('--idempotency-key', required=True)
    listing = sub.add_parser('execution-recoveries')
    listing.add_argument('--source-job-id')
    listing.add_argument('--limit', type=int, default=100)
    listing.add_argument('--before-seq', type=int)
    for name in ('execution-recovery', 'wait-execution-recovery', 'cancel-execution-recovery',
                 'export-execution-recovery', 'export-remote-execution-archive'):
        command = sub.add_parser(name)
        command.add_argument('recovery_id')
        if name.startswith('wait-'):
            command.add_argument('--timeout', type=float, default=180)
        if name.startswith('export-'):
            command.add_argument('destination')


def recovery_command(args, client):
    if args.command == 'inspect-remote-execution':
        return client.inspect_remote_execution(args.job_id)
    if args.command == 'submit-execution-recovery':
        return client.submit_execution_recovery(args.source_job_id, note=args.note, idempotency_key=args.idempotency_key)
    if args.command == 'execution-recoveries':
        return client.execution_recoveries(source_job_id=args.source_job_id, limit=args.limit, before_seq=args.before_seq)
    if args.command == 'execution-recovery':
        return client.get_execution_recovery(args.recovery_id)
    if args.command == 'wait-execution-recovery':
        return client.wait_execution_recovery(args.recovery_id, timeout=args.timeout)
    if args.command == 'cancel-execution-recovery':
        return client.cancel_execution_recovery(args.recovery_id)
    if args.command == 'export-execution-recovery':
        return _written(client.export_execution_recovery(args.recovery_id, args.destination))
    if args.command == 'export-remote-execution-archive':
        return _written(client.export_remote_execution_archive(args.recovery_id, args.destination))
    raise ValueError('Unknown recovery command')
