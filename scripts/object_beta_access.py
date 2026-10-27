#!/usr/bin/env python3
"""Prepare private participant gateways; never opens a tunnel or sends invites.

Each invited participant receives a dedicated project and a password-only
gateway backed by an Engineer credential. API/worker quota lives in the DB.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import time

import httpx
import uvicorn

from external_access_gateway import (UPSTREAM_ORIGINS, create_app, load_config,
                                     load_static, read_upstream_token)

READ_PATHS = tuple('/api/v1/' + route for route in (
    'team/whoami', 'projects', 'projects/{project_id}/object-beta-policy',
    'experiments/capabilities', 'experiments', 'experiments/executions',
    'experiments/{experiment_id}', 'experiments/executions/{job_id}',
    'experiments/executions/{job_id}/export', 'experiments/executions/{job_id}/artifacts/{artifact_id}',
    'object-sim/presets', 'object-sim/comparison', 'object-sim/story', 'object-sim/observations',
    'object-sim/executions/{execution_id}/behavior', 'object-behaviors',
    'scenario-v2/capabilities', 'scenario-v2/templates', 'scenario-v2',
    'scenario-v2/{identity}', 'scenario-v2/{identity}/export',
    'maps', 'maps/{identity}', 'assets', 'assets/{identity}', 'controllers', 'controllers/{identity}',
    'object-maps/capabilities', 'object-maps', 'object-maps/{identity}', 'object-maps/{identity}/export',
    'demo-cases',
))
WRITE_PATHS = tuple({'method': 'POST', 'path': '/api/v1/' + route} for route in (
    'experiments/preview', 'experiments', 'experiments/{experiment_id}/executions',
    'experiments/executions/{job_id}/cancel', 'object-sim/presets/{identity}/preview', 'object-sim/observations',
    'scenario-v2', 'scenario-v2/import', 'scenario-v2/{identity}/revisions',
    'scenario-v2/yaml/validate', 'scenario-v2/yaml/import', 'scenario-v2/{identity}/sweep-preview',
    'maps/{identity}/route', 'object-maps', 'object-maps/preview', 'object-maps/import',
    'object-maps/{identity}/build',
))


def private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def _response(response):
    # Never include headers, supplied credentials, or entire responses in errors.
    if not response.is_success:
        raise RuntimeError(f'Beta provisioning API returned HTTP {response.status_code}; retain the private partial workspace for recovery')
    return response.json()


def prepare(client, destination, *, upstream, dist, participants, resource_id, expires_hours=24, existing_projects=None):
    if upstream not in UPSTREAM_ORIGINS:
        raise ValueError('Use an explicitly allowed loopback API origin')
    if not 1 <= len(participants) <= 5 or len(set(participants)) != len(participants):
        raise ValueError('Provide one to five distinct participant labels')
    if any(not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', item) for item in participants):
        raise ValueError('Participant labels use lowercase letters, numbers, underscore and hyphen')
    if not 1 <= expires_hours <= 48:
        raise ValueError('Temporary beta sessions last one to 48 hours')
    if not re.fullmatch(r'carla-city-[0-9]{1,5}', resource_id) or resource_id == 'carla-city-2000':
        raise ValueError('Specify the configured dedicated CARLA city resource; shared port2000 is not supported')
    existing_projects = existing_projects or {}
    if (set(existing_projects) - set(participants) or len(set(existing_projects.values())) != len(existing_projects)
            or any(not re.fullmatch(r'project_[a-zA-Z0-9_-]+', value) for value in existing_projects.values())):
        raise ValueError('Existing projects must be distinct dedicated project IDs assigned to listed participants')
    load_static(dist, engineer=True)
    identity = _response(client.get('/api/v1/team/whoami'))
    if identity.get('mode') != 'operator':
        raise ValueError('Provisioning requires the bootstrap operator; participant sessions use Engineer tokens')
    handlers = _response(client.get('/api/v1/experiments/capabilities')).get('handlers', [])
    if not any('object_sim' in item.get('kinds', []) and item.get('resource_id') == resource_id for item in handlers):
        raise ValueError('The configured Object Sim handler does not declare this resource_id')
    destination = destination.resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    members = []
    for index, label in enumerate(participants):
        folder = destination / label
        folder.mkdir(mode=0o700)
        project = ({'id': existing_projects[label]} if label in existing_projects else
                   _response(client.post('/api/v1/projects', json={'name': 'Object Sim beta · ' + label})))
        private_json(folder / 'project.json', {'project_id': project['id'], 'label': label})
        policy = _response(client.post(f'/api/v1/projects/{project["id"]}/object-beta-policy',
                                       json={'enabled': True, 'resource_id': resource_id}))
        if not policy.get('enabled') or policy.get('active_limit') != 1 or policy.get('queue_limit') != 2:
            raise RuntimeError('Backend did not establish the required beta admission limits')
        token = _response(client.post(f'/api/v1/projects/{project["id"]}/tokens',
                                      json={'user_label': label, 'role': 'Engineer'}))
        token_file = folder / 'engineer-token'
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(token['token'] + '\n')
        private_json(folder / 'token-receipt.json', {'project_id': project['id'], 'token_id': token['id']})
        password = secrets.token_urlsafe(32)
        config = {'password': password, 'expires_at': time.time() + expires_hours * 3600,
                  'dist': str(dist.resolve()), 'upstream': upstream, 'access_mode': 'project_engineer',
                  'upstream_token_file': str(token_file), 'expected_project_id': project['id'],
                  'read_paths': READ_PATHS, 'write_paths': WRITE_PATHS}
        private_json(folder / 'gateway.json', config)
        private_json(folder / 'invitation-private.json', {'participant': label, 'password': password,
                     'suggested_loopback_port': 8863 + index, 'expires_at': config['expires_at'],
                     'start_page': '/#object-sim?project=' + project['id'],
                     'delivery': 'not_sent', 'note': 'Only share the assigned access URL and password; never share gateway/token files.'})
        members.append({'participant': label, 'project_id': project['id'], 'token_id': token['id'],
                        'gateway_config': str(folder / 'gateway.json')})
    receipt = {'schema_version': 'object-beta-access/1', 'upstream': upstream, 'participants': members,
               'service_started': False, 'invitations_sent': False}
    private_json(destination / 'participants.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    provision = commands.add_parser('prepare')
    provision.add_argument('--upstream', default='http://127.0.0.1:8063', choices=sorted(UPSTREAM_ORIGINS))
    provision.add_argument('--operator-token-file', type=Path, required=True)
    provision.add_argument('--dist', type=Path, required=True)
    provision.add_argument('--destination', type=Path, required=True)
    provision.add_argument('--participant', action='append', required=True)
    provision.add_argument('--expires-hours', type=int, default=24)
    provision.add_argument('--resource-id', required=True, help='Exact resource_id from the configured Object Sim handler capabilities')
    provision.add_argument('--existing-project', action='append', default=[], metavar='PARTICIPANT=PROJECT_ID',
                           help='Reuse a preseeded dedicated project for this participant')
    serve = commands.add_parser('serve')
    serve.add_argument('--config', type=Path, required=True)
    serve.add_argument('--port', type=int, default=8863)
    args = parser.parse_args()
    if args.command == 'serve':
        # Loopback only. HTTPS ingress, if separately authorized, terminates
        # outside this helper; gateway cookies remain Secure and HttpOnly.
        uvicorn.run(create_app(load_config(args.config.resolve())), host='127.0.0.1', port=args.port, log_level='warning')
        return
    token = read_upstream_token(args.operator_token_file.resolve())
    existing_projects = {}
    for item in args.existing_project:
        label, separator, project_id = item.partition('=')
        if not separator or label in existing_projects:
            parser.error('--existing-project requires unique PARTICIPANT=PROJECT_ID assignments')
        existing_projects[label] = project_id
    with httpx.Client(base_url=args.upstream, headers={'Authorization': 'Bearer ' + token},
                      trust_env=False, follow_redirects=False, timeout=30) as client:
        result = prepare(client, args.destination, upstream=args.upstream, dist=args.dist.resolve(),
                         participants=args.participant, expires_hours=args.expires_hours, resource_id=args.resource_id,
                         existing_projects=existing_projects)
    print(f'Prepared {len(result["participants"])} private beta participant configs. No service started or invitations sent.')


if __name__ == '__main__':
    main()
