"""Retain original camera-policy input contracts in the isolated research domain."""
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Query, Request, Response
from pydantic import Field
from sqlalchemy import select

from .base_schemas import Name, StrictModel
from .driving_contract import canonical, content_hash, preflight, validate_input
from .driving_sequence_models import DrivingSequenceRow
from .service import uid, utcnow
from .team_access import project_authorize

MAX_INPUT_BYTES=256*1024


class DrivingSequenceCreate(StrictModel):
    name: Name
    project_id: Literal['default']='default'
    research_isolated: Literal[True]
    rights_note: str=Field(min_length=1,max_length=2048)
    manifest: dict


class DrivingSequenceImport(StrictModel):
    name: Name
    project_id: Literal['default']='default'
    research_isolated: Literal[True]
    rights_note: str=Field(min_length=1,max_length=2048)
    relative_path: str=Field(min_length=1,max_length=500)


def decode_manifest(content):
    if len(content)>MAX_INPUT_BYTES:
        raise ValueError('Driving input manifest exceeds 256 KiB')
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:
                raise ValueError('Duplicate JSON key in original driving input')
            result[key]=value
        return result
    def constant(value):
        raise ValueError('Nonfinite driving input JSON constant: '+value)
    result=json.loads(content.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
    validate_input(result)
    return result


def _publish(session,name,rights_note,raw,source):
    value=decode_manifest(raw)
    identifier=uid('drive_seq')
    payload={'id':identifier,'project_id':'default','name':name,'created_at':utcnow(),
        'schema_version':'driving-sequence/1','profile':value['profile'],'research_isolated':True,
        'commercial_use':False,'rights_note':rights_note,'source':source,
        'manifest_sha256':hashlib.sha256(raw).hexdigest(),'manifest_size_bytes':len(raw),'input_hash':content_hash(value),
        'frame_count':len(value['frames']),'camera_observations':6*len(value['frames']),
        'native_file_identity_status':'requires_native_file_check','preflight':preflight(value),
        'download_url':f'/api/v1/driving-sequences/{identifier}/export'}
    session.add(DrivingSequenceRow(id=identifier,project_id='default',created_at=payload['created_at'],
        manifest_sha256=payload['manifest_sha256'],input_hash=payload['input_hash'],manifest=raw,payload=payload))
    session.flush()
    return payload|{'manifest':value}


def driving_sequence_get(session,identifier):
    row=session.get(DrivingSequenceRow,identifier)
    if row is None:
        raise HTTPException(404,'Driving sequence not found')
    try:
        value=decode_manifest(row.manifest)
        if (hashlib.sha256(row.manifest).hexdigest()!=row.manifest_sha256 or content_hash(value)!=row.input_hash
                or row.payload['id']!=row.id or row.payload['manifest_sha256']!=row.manifest_sha256
                or row.payload['input_hash']!=row.input_hash or row.payload['manifest_size_bytes']!=len(row.manifest)
                or row.project_id!='default' or row.payload['project_id']!=row.project_id
                or row.payload['research_isolated'] is not True or row.payload['commercial_use'] is not False):
            raise ValueError('Original driving manifest or identity differs')
        return row,row.payload|{'manifest':value}
    except (ValueError,TypeError,KeyError) as error:
        raise HTTPException(409,'Driving sequence integrity check failed') from error


def configured_input_file(relative):
    root_value=os.environ.get('AXIOM_DRIVING_INPUT_ROOT','').strip()
    if not root_value:
        raise ValueError('Operator must configure AXIOM_DRIVING_INPUT_ROOT before file import')
    root=Path(root_value).resolve()
    fragment=Path(relative)
    if fragment.is_absolute() or '..' in fragment.parts or '\\' in relative or ':' in relative or fragment.suffix!='.json':
        raise ValueError('Select a bounded relative JSON path inside the configured input root')
    current=root
    for part in fragment.parts:
        current=current/part
        if current.is_symlink():
            raise ValueError('Symlink input components are not accepted')
    resolved=current.resolve()
    resolved.relative_to(root)
    if not root.is_dir() or not resolved.is_file() or resolved.stat().st_size>MAX_INPUT_BYTES:
        raise ValueError('Configured driving JSON source is missing or oversized')
    with resolved.open('rb') as stream:
        raw=stream.read(MAX_INPUT_BYTES+1)
    decode_manifest(raw)
    return raw


def register_driving_sequence_routes(api,db):
    @api.post('/driving-sequences',status_code=201,tags=['driving-research'])
    def create(body:DrivingSequenceCreate,request:Request):
        project_authorize(request,db,'default','Engineer')
        try:
            raw=canonical(body.manifest)
            with db.session.begin() as session:
                return _publish(session,body.name,body.rights_note,raw,{'type':'submitted_input_manifest','native_files_checked':False})
        except (ValueError,TypeError) as error:
            raise HTTPException(422,str(error)) from error

    @api.post('/driving-sequences/import',status_code=201,tags=['driving-research'])
    def import_manifest(body:DrivingSequenceImport,request:Request):
        project_authorize(request,db,'default','Engineer')
        try:
            raw=configured_input_file(body.relative_path)
            with db.session.begin() as session:
                return _publish(session,body.name,body.rights_note,raw,{'type':'configured_input_root',
                    'relative_path':body.relative_path,'native_files_checked':False})
        except (ValueError,TypeError,OSError) as error:
            raise HTTPException(422,str(error)) from error

    @api.get('/driving-sequences',tags=['driving-research'])
    def list_sequences(request:Request,limit:int=Query(50,ge=1,le=100)):
        project_authorize(request,db,'default','Viewer')
        with db.read_session() as session:
            rows=session.scalars(select(DrivingSequenceRow).where(DrivingSequenceRow.project_id=='default')
                .order_by(DrivingSequenceRow.created_at.desc(),DrivingSequenceRow.id).limit(limit))
            return {'items':[row.payload for row in rows]}

    @api.get('/driving-sequences/{identifier}',tags=['driving-research'])
    def sequence(identifier:str,request:Request):
        project_authorize(request,db,'default','Viewer')
        with db.read_session() as session:
            return driving_sequence_get(session,identifier)[1]

    @api.get('/driving-sequences/{identifier}/export',tags=['driving-research'])
    def export(identifier:str,request:Request):
        project_authorize(request,db,'default','Viewer')
        with db.read_session() as session:
            row,detail=driving_sequence_get(session,identifier)
            return Response(row.manifest,media_type='application/json',headers={'X-Content-SHA256':detail['manifest_sha256'],
                'Content-Disposition':f'attachment; filename="{identifier}.json"'})

