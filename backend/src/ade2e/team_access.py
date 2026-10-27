"""Explicit project permissions. Legacy resource domains reject project tokens."""
from dataclasses import dataclass
import hashlib
import secrets
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .team_models import ProjectRow, ProjectTokenRow
from .service import uid, utcnow

ROLES = {'Viewer', 'Engineer', 'Reviewer', 'Admin'}
TEAM_PREFIXES = ('/api/v1/beta', '/api/v1/demo-cases', '/api/v1/neural-sim', '/api/v1/hil-sim', '/api/v1/virtual-ecu', '/api/v1/virtual-ecu-network', '/api/v1/log-sim', '/api/v1/object-sim', '/api/v1/sensor-sim', '/api/v1/object-maps', '/api/v1/object-behaviors', '/api/v1/projects', '/api/v1/experiments', '/api/v1/scenario-v2', '/api/v1/replay-specs',
                 '/api/v1/maps', '/api/v1/map-sources', '/api/v1/assets', '/api/v1/controllers',
                 '/api/v1/scene-sequences', '/api/v1/sensor-captures', '/api/v1/camera-bridge',
                 '/api/v1/experiment-verification-plans', '/api/v1/driving-sequences', '/api/v1/project-recordings')

@dataclass(frozen=True)
class Principal:
    mode: str
    project_id: str | None = None
    role: str = 'Admin'
    user_label: str = 'local-operator'
    token_id: str | None = None

def authenticate_request(request: Request, db, settings, supplied_token: str | None):
    if settings.api_token and supplied_token and secrets.compare_digest(supplied_token, settings.api_token):
        principal = Principal('operator', user_label='bootstrap-operator')
    elif supplied_token:
        token_hash = hashlib.sha256(supplied_token.encode()).hexdigest()
        with db.read_session() as session:
            token = session.scalar(select(ProjectTokenRow).where(ProjectTokenRow.token_sha256 == token_hash,
                                                                 ProjectTokenRow.revoked_at.is_(None)))
            if token is None:
                raise HTTPException(401, 'Invalid or revoked access token')
            principal = Principal('project', token.project_id, token.role, token.user_label, token.id)
    elif not settings.api_token and not settings.team_mode:
        principal = Principal('legacy')
    else:
        raise HTTPException(401, 'A project access token or operator bootstrap token is required')
    if principal.mode == 'project':
        path = request.url.path.rstrip('/')
        allowed = path in {'/api/v1/team/whoami', '/api/v1/agent/tools'} or any(
            path == prefix or path.startswith(prefix + '/') for prefix in TEAM_PREFIXES)
        if not allowed:
            raise HTTPException(403, 'Legacy resource APIs have no project boundary; use the operator profile for those domains')
    request.state.principal = principal
    return principal

def project_authorize(request: Request, db, project_id: str, minimum_role: str = 'Viewer'):
    principal = getattr(request.state, 'principal', None)
    if principal is None:
        # Registration without the central authentication dependency is unsafe.
        raise HTTPException(401, 'Authenticated project context is required')
    with db.read_session() as session:
        if session.get(ProjectRow, project_id) is None:
            raise HTTPException(404, 'Project not found')
    if principal.mode != 'project':
        return principal
    if principal.project_id != project_id:
        raise HTTPException(404, 'Project resource not found')
    allowed = {'Viewer': ROLES, 'Engineer': {'Engineer', 'Admin'},
               'Reviewer': {'Reviewer', 'Admin'}, 'Admin': {'Admin'}}
    if minimum_role not in allowed:
        raise ValueError('Unknown project permission')
    if principal.role not in allowed[minimum_role]:
        raise HTTPException(403, f'{minimum_role} permission is required for this operation')
    return principal

class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=180)

class TokenCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_label: str = Field(min_length=1, max_length=180)
    role: str = Field(pattern='^(Viewer|Engineer|Reviewer|Admin)$')

def register_team_routes(api, db):
    from .object_beta_access import register_object_beta_access_routes
    register_object_beta_access_routes(api, db)

    @api.get('/team/whoami', tags=['projects'])
    def whoami(request: Request):
        principal = request.state.principal
        return {'mode': principal.mode, 'project_id': principal.project_id, 'role': principal.role,
                'principal_id': hashlib.sha256(('axiom-principal-v1:' + (principal.token_id or principal.mode)).encode()).hexdigest(),
                'user_label': principal.user_label, 'legacy_domains_available': principal.mode != 'project',
                'permissions': {'read': True, 'execute': principal.role in {'Engineer', 'Admin'},
                                'review': principal.role in {'Reviewer', 'Admin'}, 'admin': principal.role == 'Admin'}}

    @api.get('/projects', tags=['projects'])
    def project_list(request: Request):
        principal = request.state.principal
        with db.read_session() as session:
            query = select(ProjectRow).order_by(ProjectRow.created_at, ProjectRow.id)
            if principal.mode == 'project':
                query = query.where(ProjectRow.id == principal.project_id)
            return {'items': [{'id': row.id, 'name': row.name, 'created_at': row.created_at}
                              for row in session.scalars(query)]}

    @api.post('/projects', status_code=201, tags=['projects'],
              openapi_extra={'x-axiom-min-role': 'Admin', 'x-axiom-operator-only': True})
    def project_create(body: ProjectCreate, request: Request):
        if request.state.principal.mode == 'project':
            raise HTTPException(403, 'Only the operator can create project boundaries')
        with db.session() as session:
            row = ProjectRow(id=uid('project'), name=body.name, created_at=utcnow())
            session.add(row)
            session.commit()
            return {'id': row.id, 'name': row.name, 'created_at': row.created_at}

    @api.post('/projects/{project_id}/tokens', status_code=201, tags=['projects'], openapi_extra={'x-axiom-min-role': 'Admin'})
    def token_create(project_id: str, body: TokenCreate, request: Request):
        project_authorize(request, db, project_id, 'Admin')
        plaintext = 'axiom_' + secrets.token_urlsafe(32)
        with db.session() as session:
            row = ProjectTokenRow(id=uid('token'), project_id=project_id, user_label=body.user_label,
                                  role=body.role, token_sha256=hashlib.sha256(plaintext.encode()).hexdigest(), created_at=utcnow())
            session.add(row)
            session.commit()
            return {'id': row.id, 'project_id': project_id, 'user_label': row.user_label, 'role': row.role,
                    'created_at': row.created_at, 'token': plaintext, 'display_once': True}

    @api.get('/projects/{project_id}/tokens', tags=['projects'], openapi_extra={'x-axiom-min-role': 'Admin'})
    def tokens(project_id: str, request: Request):
        project_authorize(request, db, project_id, 'Admin')
        with db.read_session() as session:
            return {'items': [{'id': row.id, 'user_label': row.user_label, 'role': row.role,
                              'created_at': row.created_at, 'revoked_at': row.revoked_at}
                             for row in session.scalars(select(ProjectTokenRow).where(ProjectTokenRow.project_id == project_id))]}

    @api.post('/projects/{project_id}/tokens/{token_id}/revoke', tags=['projects'], openapi_extra={'x-axiom-min-role': 'Admin'})
    def revoke(project_id: str, token_id: str, request: Request):
        project_authorize(request, db, project_id, 'Admin')
        with db.session() as session:
            row = session.get(ProjectTokenRow, token_id)
            if row is None or row.project_id != project_id:
                raise HTTPException(404, 'Project token not found')
            row.revoked_at = row.revoked_at or utcnow()
            session.commit()
            return {'id': row.id, 'revoked_at': row.revoked_at}
