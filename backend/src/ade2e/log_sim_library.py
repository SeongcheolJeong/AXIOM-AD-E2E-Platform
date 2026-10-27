"""Operator-prepared real-log corpus with original-scene denominators."""
from collections import Counter
from threading import RLock

from fastapi import HTTPException, Request
from pydantic import Field, model_validator
from sqlalchemy import select

from . import log_sim
from .base_schemas import StrictModel
from .log_sim_replays import _put, _values
from .project_recordings import authorize_recording_source, recording_source_scope
from .recording_models import RecordingChannelRow, RecordingMessageRow, RecordingRow, RecordingSchemaRow
from .service import digest, utcnow
from .team_access import project_authorize
from .simulation_catalog_models import SimulationCatalogRevisionRow as Catalog

KIND = 'log-sim-library'
LOCK = RLock()
Hash = str


class ExcludedFrame(StrictModel):
    index: int = Field(ge=0, le=63, strict=True)
    reason: str = Field(min_length=1, max_length=200)


class LibrarySegment(StrictModel):
    recording_id: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    sample_start_index: int = Field(ge=0, le=63, strict=True)
    sample_end_index: int = Field(ge=0, le=63, strict=True)
    start_time_s: float = Field(ge=0, allow_inf_nan=False)
    end_time_s: float = Field(ge=0, allow_inf_nan=False)


class LibraryScene(StrictModel):
    scene_token: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=2000)
    original_scene_record_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    original_frame_count: int = Field(ge=2, le=64, strict=True)
    duration_s: float = Field(gt=0, allow_inf_nan=False)
    excluded: list[ExcludedFrame] = Field(default_factory=list, max_length=64)
    segments: list[LibrarySegment] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def partition(self):
        indices = [frame.index for frame in self.excluded]
        for segment in self.segments:
            if segment.sample_end_index <= segment.sample_start_index or segment.end_time_s <= segment.start_time_s:
                raise ValueError('Every retained interval requires at least two ordered original frames')
            if segment.end_time_s > self.duration_s + .000001:
                raise ValueError('Segment lies outside the original scene')
            if (segment.sample_end_index == self.original_frame_count - 1
                    and abs(segment.end_time_s - self.duration_s) > .000001):
                raise ValueError('Original scene duration differs from its retained final frame')
            indices.extend(range(segment.sample_start_index, segment.sample_end_index + 1))
        if sorted(indices) != list(range(self.original_frame_count)):
            raise ValueError('Original frames must be partitioned exactly once into retained intervals and exclusions')
        return self


class LibraryCreate(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    original_archive_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    scenes: list[LibraryScene] = Field(min_length=1, max_length=10)

    @model_validator(mode='after')
    def unique(self):
        if len({scene.scene_token for scene in self.scenes}) != len(self.scenes):
            raise ValueError('An original scene can occur only once in the library')
        ids = [seg.recording_id for scene in self.scenes for seg in scene.segments]
        if len(ids) != len(set(ids)) or len(ids) > 64:
            raise ValueError('Library requires at most 64 distinct original recording intervals')
        return self


def library_snapshot(session, project_id, library_id=None):
    if library_id is None:
        values = _values(session, project_id, KIND)
        if not values:
            return dict(schema_version='log-sim-library/1', project_id=project_id, id=None, library_id=None,
                manifest_hash=None, items=[], counts=dict(scene_count=0, segment_count=0,
                original_frame_count=0, available_frame_count=0, excluded_frame_count=0),
                scope='isolated_noncommercial_research')
        library_id = values[0]['library_id']
    row = log_sim._catalog(session, project_id, KIND, library_id)
    if row is None:
        raise HTTPException(404, 'Log library not found in this project')
    value = row['definition']
    for scene in value['items']:
        for segment in scene['segments']:
            source = log_sim._source(session, project_id, segment['source_id'])
            if source['source_hash'] != segment['source_hash']:
                raise HTTPException(409, 'Library source changed after registration')
    return value | {'id': library_id, 'manifest_hash': row['manifest_hash']}


def _authorize(request, db, value):
    project_authorize(request, db, value['project_id'], 'Viewer')
    for item in value['items']:
        for segment in item['segments']:
            authorize_recording_source(request, db, value['project_id'], segment['recording_id'])


def register_library(db, body):
    """Validate all pinned provenance before registering any new source binding."""
    request_hash = digest(body.model_dump(mode='json'))
    identity = 'loglib_' + request_hash[:32]
    with LOCK:
        with db.read_session() as session:
            if log_sim._catalog(session, body.project_id, KIND, identity):
                return library_snapshot(session, body.project_id, identity)
            for scene in body.scenes:
                for segment in scene.segments:
                    recording = session.get(RecordingRow, segment.recording_id)
                    if recording is None or recording.sha256 != segment.sha256:
                        raise HTTPException(409, 'Library segment recording SHA-256 differs')
                    scope = recording_source_scope(recording)
                    lineage = scope.get('lineage', {})
                    expected = dict(scene_token=scene.scene_token, scene_name=scene.name, description=scene.description,
                        original_scene_record_sha256=scene.original_scene_record_sha256,
                        original_frame_count=scene.original_frame_count, sample_start_index=segment.sample_start_index,
                        sample_end_index=segment.sample_end_index, start_time_s=segment.start_time_s, end_time_s=segment.end_time_s)
                    if (not scope.get('full_lineage_metadata_verified') or lineage.get('library_metadata') != expected
                            or lineage.get('original_archive', {}).get('sha256') != body.original_archive_sha256):
                        raise HTTPException(422, 'Library metadata must match retained original scene provenance')
                    schemas = {row.schema_id: row.payload['name'] for row in session.scalars(
                        select(RecordingSchemaRow).where(RecordingSchemaRow.recording_id == segment.recording_id))}
                    channels = list(session.scalars(select(RecordingChannelRow).where(
                        RecordingChannelRow.recording_id == segment.recording_id)))
                    roles = {}
                    for name in ('axiom.PlanningEgo.v1', 'axiom.ObjectTracks.v1', 'foxglove.CompressedImage'):
                        matches = [row for row in channels if schemas.get(row.schema_ref) == name]
                        if len(matches) != 1:
                            raise HTTPException(422, 'Library source requires one original '+name+' channel')
                        roles[name] = matches[0].channel_id
                    clocks = list(session.scalars(select(RecordingMessageRow.log_time_key).where(
                        RecordingMessageRow.recording_id == segment.recording_id,
                        RecordingMessageRow.channel_id == roles['axiom.PlanningEgo.v1'])
                        .order_by(RecordingMessageRow.log_time_key, RecordingMessageRow.ordinal)))
                    count = segment.sample_end_index - segment.sample_start_index + 1
                    if (len(clocks) != count or not 2 <= count <= 64
                            or any(int(b) <= int(a) for a, b in zip(clocks, clocks[1:]))
                            or abs((int(clocks[-1])-int(clocks[0]))/1e9
                                   - (segment.end_time_s-segment.start_time_s)) > .000001):
                        raise HTTPException(422, 'Recording interval differs from original scene frame selection')
        items = []
        for scene in body.scenes:
            segments = []
            thumbnail = None
            for segment in scene.segments:
                metadata = dict(scene_token=scene.scene_token, scene_name=scene.name, description=scene.description,
                    original_scene_record_sha256=scene.original_scene_record_sha256,
                    original_frame_count=scene.original_frame_count, sample_start_index=segment.sample_start_index,
                    sample_end_index=segment.sample_end_index, start_time_s=segment.start_time_s, end_time_s=segment.end_time_s)
                stored = log_sim.register_road_source(db, body.project_id, segment.recording_id,
                    title=f'{scene.name} · {segment.sample_start_index + 1}–{segment.sample_end_index + 1}', library_metadata=metadata)
                definition = stored['definition']
                count = segment.sample_end_index - segment.sample_start_index + 1
                if definition['frame_count'] != count or abs(definition['duration_s'] - (segment.end_time_s - segment.start_time_s)) > .000001:
                    raise HTTPException(422, 'Recording interval differs from original scene frame selection')
                segments.append(dict(source_id=stored['id'], recording_id=segment.recording_id,
                    title=definition['title'], frame_count=count, duration_s=definition['duration_s'],
                    start_time_s=segment.start_time_s, end_time_s=segment.end_time_s, source_hash=stored['manifest_hash']))
                if thumbnail is None:
                    with db.read_session() as session:
                        camera = session.scalar(select(RecordingMessageRow).where(
                            RecordingMessageRow.recording_id == segment.recording_id,
                            RecordingMessageRow.channel_id == definition['recording_ref']['camera_channel_id'])
                            .order_by(RecordingMessageRow.log_time_key, RecordingMessageRow.ordinal).limit(1))
                        if camera:
                            thumbnail = f'/api/v1/log-sim/sources/{stored["id"]}/images/{camera.ordinal}?project_id={body.project_id}'
            available = sum(segment['frame_count'] for segment in segments)
            exclusions = Counter(frame.reason for frame in scene.excluded)
            items.append(dict(id=scene.scene_token, name=scene.name, title=scene.name, description=scene.description,
                description_source='original_dataset_metadata', source_kind='nuscenes',
                original_frame_count=scene.original_frame_count, available_frame_count=available,
                excluded_frame_count=len(scene.excluded), duration_s=scene.duration_s,
                status='unavailable' if not available else 'partial' if scene.excluded else 'ready',
                exclusions=[dict(reason=reason, frame_count=count) for reason, count in sorted(exclusions.items())],
                excluded_frames=[frame.model_dump() for frame in scene.excluded], thumbnail_url=thumbnail, segments=segments))
        value = dict(schema_version='log-sim-library/1', project_id=body.project_id, library_id=identity,
            original_archive_sha256=body.original_archive_sha256, items=items, created_at=utcnow(),
            counts=dict(scene_count=len(items), segment_count=sum(len(item['segments']) for item in items),
                original_frame_count=sum(item['original_frame_count'] for item in items),
                available_frame_count=sum(item['available_frame_count'] for item in items),
                excluded_frame_count=sum(item['excluded_frame_count'] for item in items)),
            scope='isolated_noncommercial_research')
        with db.session.begin() as session:
            _put(session, body.project_id, KIND, identity, value)
        with db.read_session() as session:
            return library_snapshot(session, body.project_id, identity)


def register_log_sim_library_routes(api, db):
    @api.get('/log-sim/library', tags=['Log Sim'])
    def get_library(request: Request, project_id: str = 'default', library_id: str | None = None):
        project_authorize(request, db, project_id, 'Viewer')
        with db.read_session() as session:
            value = library_snapshot(session, project_id, library_id)
        _authorize(request, db, value)
        if library_id is not None:
            return value
        with db.read_session() as session:
            ids=set(session.scalars(select(Catalog.id).where(Catalog.project_id==project_id,Catalog.kind==log_sim.SOURCE_KIND)))
            grouped={}
            for identity in sorted(ids):
                stored=log_sim._catalog(session,project_id,log_sim.SOURCE_KIND,identity)['definition']
                if stored.get('kind')!='recorded': continue
                source=log_sim._source(session,project_id,identity);ref=source['recording_ref']
                authorize_recording_source(request,db,project_id,ref['id'])
                item=grouped.setdefault(ref['id'],dict(id=ref['id'],name=stored['title'],title=stored['title'],
                    description='사용자가 등록한 원본 MCAP · 객체 생성 출처는 원본 선언을 확인하세요.',
                    description_source='user_upload',source_kind='recorded',original_frame_count=stored['original_frame_count'],
                    available_frame_count=0,excluded_frame_count=0,duration_s=0,status='ready',exclusions=[],thumbnail_url=None,segments=[]))
                item['available_frame_count']+=stored['frame_count'];item['duration_s']+=stored['duration_s']
                item['segments'].append(dict(source_id=identity,recording_id=ref['id'],title=source['title'],frame_count=source['frame_count'],
                    duration_s=source['duration_s'],start_time_s=0,end_time_s=source['duration_s'],source_hash=source['source_hash']))
            extra=list(grouped.values())
            for item in extra:
                item['excluded_frame_count']=max(0,item['original_frame_count']-item['available_frame_count'])
                if item['excluded_frame_count']:
                    item['status']='partial';item['exclusions']=[dict(reason='not_selected_for_analysis',frame_count=item['excluded_frame_count'])]
        if extra:
            value=value|{'items':value['items']+extra,'counts':dict(value['counts'])}
            value['counts']['scene_count']+=len(extra)
            value['counts']['segment_count']+=sum(len(i['segments']) for i in extra)
            for key in ('original_frame_count','available_frame_count','excluded_frame_count'):
                value['counts'][key]+=sum(i[key] for i in extra)
        return value

    @api.post('/log-sim/library', status_code=201, tags=['Log Sim'],
              openapi_extra={'x-axiom-min-role': 'Admin', 'x-axiom-operator-only': True})
    def create_library(body: LibraryCreate, request: Request):
        principal = project_authorize(request, db, body.project_id, 'Admin')
        if principal.mode == 'project':
            raise HTTPException(403, 'Only the operator can register a prepared original log library')
        for scene in body.scenes:
            for segment in scene.segments:
                authorize_recording_source(request, db, body.project_id, segment.recording_id)
        return register_library(db, body)
