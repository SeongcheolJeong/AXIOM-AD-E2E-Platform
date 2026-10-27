#!/usr/bin/env python3
"""Prepare a private ADP beta gateway from an existing project credential.

No project creation, service restart, public ingress or invitation delivery.
Each config targets exactly one service and one project. Existing Object beta
provisioning remains available separately for its explicit CARLA admission policy.
"""
import argparse
from pathlib import Path
import re
import secrets
import time

import httpx

from external_access_gateway import UPSTREAM_ORIGINS, load_static, read_upstream_token
from object_beta_access import READ_PATHS as OBJECT_READ, WRITE_PATHS as OBJECT_WRITE, private_json

BETA_READ = tuple('/api/v1/beta/' + path for path in ('readiness', 'evidence', 'tasks', 'tasks/{identity}', 'feedback'))
BETA_WRITE = tuple({'method': 'POST', 'path': '/api/v1/beta/' + path}
                   for path in ('tasks', 'tasks/{identity}/progress', 'feedback'))

LOG_READ = tuple('/api/v1/log-sim/' + path for path in (
    'sources', 'sources/{identity}', 'sources/{identity}/images/{ordinal}',
    'sources/{identity}/events', 'sources/{identity}/cases/{case_id}', 'variants',
    'library', 'packs', 'example.mcap', 'uploads', 'comparison-settings',
    'comparison-settings/{identity}', 'reviews', 'beta-tasks', 'beta-tasks/{identity}',
    'beta-feedback', 'replay-profiles', 'comparisons', 'comparisons/{identity}',
    'batches', 'batches/{identity}', 'batches/{identity}/export',
    'case-bundles', 'case-bundles/{identity}', 'regressions',
))
LOG_WRITE = tuple({'method': 'POST', 'path': '/api/v1/log-sim/' + path} for path in (
    'sources/{identity}/cases', 'variants/preview', 'variants', 'inputs/inspect', 'inputs',
    'comparison-settings', 'reviews', 'beta-tasks', 'beta-tasks/{identity}/progress', 'beta-feedback',
    'comparisons/preview', 'comparisons', 'batches/preview', 'batches', 'batches/{identity}/retry',
    'batches/{identity}/case-bundles',
))
HIL_READ = tuple('/api/v1/hil-sim/' + path for path in (
    'scenes', 'presets', 'dut-profiles', 'dut-profiles/{identity}', 'tests', 'tests/{identity}',
    'tests/{identity}/executions', 'tests/{identity}/export', 'observations',
    'batches', 'batches/{identity}', 'batches/{identity}/export',
))
HIL_WRITE = tuple({'method': 'POST', 'path': '/api/v1/hil-sim/' + path} for path in (
    'presets/{identity}/preview', 'dut-profiles', 'tests', 'tests/{identity}/preview',
    'tests/{identity}/executions', 'observations', 'batches', 'batches/{identity}/preview',
    'batches/{identity}/start', 'batches/{identity}/resume', 'batches/{identity}/cancel',
))


def approved_paths():
    reads = tuple(dict.fromkeys((*OBJECT_READ, *BETA_READ, *LOG_READ, *HIL_READ,
        '/api/v1/sensor-sim/presets', '/api/v1/neural-sim/presets', '/api/v1/neural-sim/sources')))
    writes = tuple({(item['method'], item['path']): item
                    for item in (*OBJECT_WRITE, *BETA_WRITE, *LOG_WRITE, *HIL_WRITE)}.values())
    return reads, writes


def prepare(client, destination, *, upstream, dist, project_id, token_file, expires_hours=24):
    if upstream not in UPSTREAM_ORIGINS:
        raise ValueError('Use an explicitly allowed loopback API origin')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', project_id):
        raise ValueError('Invalid project ID')
    if not 1 <= expires_hours <= 48:
        raise ValueError('Temporary beta sessions last one to 48 hours')
    token_file = token_file.resolve()
    read_upstream_token(token_file)
    load_static(dist.resolve(), engineer=True)
    response = client.get('/api/v1/team/whoami')
    if not response.is_success:
        raise ValueError('Project credential could not be verified')
    identity = response.json()
    if (identity.get('mode') != 'project' or identity.get('role') != 'Engineer'
            or identity.get('project_id') != project_id):
        raise ValueError('Use the existing Engineer credential for this exact project')
    destination = destination.resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    reads, writes = approved_paths()
    password = secrets.token_urlsafe(32)
    config = dict(password=password, expires_at=time.time() + expires_hours * 3600,
        dist=str(dist.resolve()), upstream=upstream, upstream_token_file=str(token_file),
        access_mode='project_engineer', expected_project_id=project_id,
        read_paths=reads, write_paths=writes)
    private_json(destination / 'gateway.json', config)
    private_json(destination / 'invitation-private.json', dict(password=password,
        expires_at=config['expires_at'], start_page='/#workspace?project=' + project_id,
        delivery='not_sent', note='Share only assigned URL and password, never token/config files.'))
    receipt = dict(schema_version='beta-workspace-access/1', project_id=project_id,
        gateway_config=str(destination / 'gateway.json'), service_started=False, invitations_sent=False,
        limitations=['Prepared Log packs only; binary MCAP upload uses the direct authenticated service.',
                     'Sensor and Neural gateway access is retained playback and feedback only.',
                     'Existing project execution policies and engine availability still apply.'])
    private_json(destination / 'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, choices=sorted(UPSTREAM_ORIGINS))
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--engineer-token-file', type=Path, required=True)
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--expires-hours', type=int, default=24)
    args = parser.parse_args()
    token = read_upstream_token(args.engineer_token_file.resolve())
    with httpx.Client(base_url=args.upstream, headers={'Authorization': 'Bearer ' + token},
                      trust_env=False, follow_redirects=False, timeout=20) as client:
        prepare(client, args.destination, upstream=args.upstream, dist=args.dist,
                project_id=args.project_id, token_file=args.engineer_token_file, expires_hours=args.expires_hours)
    print('Prepared private beta gateway configuration. No service started or invitation sent.')


if __name__ == '__main__':
    main()
