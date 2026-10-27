"""CLI for complete bounded sensor recipes and immutable revision manifests."""
import json
from pathlib import Path

COMMANDS = {'simulation-sensor-capabilities', 'simulation-sensor-configurations',
    'simulation-sensor-configuration', 'simulation-sensor-configuration-revisions',
    'create-simulation-sensor-configuration', 'revise-simulation-sensor-configuration',
    'export-simulation-sensor-configuration'}


def register_simulation_sensor_commands(sub):
    sub.add_parser('simulation-sensor-capabilities').add_argument('--adapter-id')
    sub.add_parser('simulation-sensor-configurations')
    for name in ('simulation-sensor-configuration', 'simulation-sensor-configuration-revisions',
                 'export-simulation-sensor-configuration'):
        parser = sub.add_parser(name)
        parser.add_argument('configuration_id')
        if name != 'simulation-sensor-configuration-revisions': parser.add_argument('--revision', type=int)
        if name == 'export-simulation-sensor-configuration': parser.add_argument('destination')
    for name in ('create-simulation-sensor-configuration', 'revise-simulation-sensor-configuration'):
        parser = sub.add_parser(name)
        if name.startswith('revise'):
            parser.add_argument('configuration_id')
            parser.add_argument('--expected-revision', required=True, type=int)
        parser.add_argument('definition', help='Complete recipe JSON file; noise_seed comes only from the effective scenario')
        parser.add_argument('--idempotency-key', required=True)


def simulation_sensor_command(args, client):
    command = args.command
    if command == 'simulation-sensor-capabilities': return client.simulation_sensor_capabilities(adapter_id=args.adapter_id)
    if command == 'simulation-sensor-configurations': return client.simulation_sensor_configurations()
    if command == 'simulation-sensor-configuration': return client.get_simulation_sensor_configuration(args.configuration_id, revision=args.revision)
    if command == 'simulation-sensor-configuration-revisions': return client.simulation_sensor_configuration_revisions(args.configuration_id)
    if command == 'export-simulation-sensor-configuration':
        return {'path': str(client.export_simulation_sensor_configuration(args.configuration_id, args.destination, revision=args.revision).resolve())}
    with Path(args.definition).open('rb') as stream: content = stream.read(65537)
    if len(content)>65536: raise ValueError('Sensor configuration definition exceeds 64 KiB')
    definition = json.loads(content)
    if not isinstance(definition, dict): raise ValueError('Sensor configuration must be a JSON object')
    if 'expected_revision' in definition or 'idempotency_key' in definition:
        raise ValueError('Use explicit command flags for revision and idempotency key')
    if command == 'create-simulation-sensor-configuration':
        return client.create_simulation_sensor_configuration(idempotency_key=args.idempotency_key, **definition)
    return client.revise_simulation_sensor_configuration(args.configuration_id, expected_revision=args.expected_revision,
        idempotency_key=args.idempotency_key, **definition)
