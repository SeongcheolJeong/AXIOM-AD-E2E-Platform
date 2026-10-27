"""Editable road documents, separate from compiled engine map revisions."""
from typing import Literal
from pydantic import Field, model_validator
from .base_schemas import Name, StrictModel
from .scenario_v2_schemas import CityPose, Identity
from .simulation_catalog_schemas import Rights, Sha256, OpenDriveContent


class RoadGeometry(StrictModel):
    kind: Literal['line','arc','spiral'] = 'line'
    length_m: float = Field(default=80, ge=1, le=2000)
    curvature_start: float = Field(default=0, ge=-.2, le=.2)
    curvature_end: float = Field(default=0, ge=-.2, le=.2)

    @model_validator(mode='after')
    def shape(self):
        if self.kind=='line' and (self.curvature_start or self.curvature_end):
            raise ValueError('A line has zero curvature')
        if self.kind=='arc' and (not self.curvature_start or self.curvature_start!=self.curvature_end):
            raise ValueError('An arc requires equal, nonzero start/end curvature')
        return self


class RoadLane(StrictModel):
    id: int = Field(ge=-6, le=6)
    width_m: float = Field(default=3.5, ge=2, le=6)
    speed_limit_mps: float = Field(default=12, gt=0, le=12)
    lane_change: Literal['none','both','left','right'] = 'both'
    closed: bool = False
    priority: int = Field(default=0, ge=0, le=100)

    @model_validator(mode='after')
    def nonzero(self):
        if self.id==0: raise ValueError('Driving lane 0 is reserved for the reference line')
        return self


class AuthoredRoad(StrictModel):
    id: Identity
    name: str = Field(default='', max_length=180)
    origin: CityPose = Field(default_factory=lambda:CityPose(x=0,y=0,z=0,heading_rad=0))
    geometries: list[RoadGeometry] = Field(default_factory=lambda:[RoadGeometry()], min_length=1, max_length=32)
    lanes: list[RoadLane] = Field(default_factory=lambda:[RoadLane(id=-1)], min_length=1, max_length=12)
    junction_id: Identity | None = None

    @model_validator(mode='after')
    def contiguous_lanes(self):
        ids=[lane.id for lane in self.lanes]
        if len(set(ids))!=len(ids): raise ValueError('Road lane IDs must be unique')
        for sign in (-1,1):
            side=sorted(abs(i) for i in ids if i*sign>0)
            if side!=list(range(1,len(side)+1)): raise ValueError('Lane IDs must be contiguous from -1 or +1')
        return self


class RoadConnection(StrictModel):
    from_road: Identity
    from_lane: int
    to_road: Identity
    to_lane: int


class RoadJunction(StrictModel):
    id: Identity
    name: str = Field(default='', max_length=180)


class RoadSignal(StrictModel):
    id: Identity
    road_id: Identity
    s_m: float = Field(ge=0, le=64000)
    t_m: float = Field(default=0, ge=-100, le=100, description='Right-positive lateral offset in authored CARLA coordinates')
    kind: Literal['traffic_light','stop','yield'] = 'stop'
    orientation: Literal['+','-'] = '+'
    lane_ids: list[int] = Field(default_factory=list, max_length=12)


class RoadObject(StrictModel):
    id: Identity
    road_id: Identity
    s_m: float = Field(ge=0, le=64000)
    t_m: float = Field(default=0, ge=-100, le=100)
    length_m: float = Field(default=1, gt=0, le=50)
    width_m: float = Field(default=1, gt=0, le=50)
    height_m: float = Field(default=1, gt=0, le=30)


class ObjectMapDefinition(StrictModel):
    schema_version: Literal['object-map/1'] = 'object-map/1'
    name: Name
    roads: list[AuthoredRoad] = Field(default_factory=list, max_length=128)
    connections: list[RoadConnection] = Field(default_factory=list, max_length=2048)
    junctions: list[RoadJunction] = Field(default_factory=list, max_length=128)
    signals: list[RoadSignal] = Field(default_factory=list, max_length=512)
    objects: list[RoadObject] = Field(default_factory=list, max_length=512)
    rights: Rights
    read_only: bool = False
    unsupported: list[str] = Field(default_factory=list, max_length=256)
    original_opendrive: OpenDriveContent | None = None

    @model_validator(mode='after')
    def references(self):
        for name in ('roads','junctions','signals','objects'):
            ids=[v.id for v in getattr(self,name)]
            if len(ids)!=len(set(ids)): raise ValueError(f'{name} IDs must be unique')
        roads={r.id:r for r in self.roads};junctions={j.id for j in self.junctions}
        for road in self.roads:
            if road.junction_id and road.junction_id not in junctions: raise ValueError('Road references an unknown junction')
        connections=set()
        for c in self.connections:
            key=(c.from_road,c.from_lane,c.to_road,c.to_lane)
            if key in connections: raise ValueError('Duplicate road/lane connection')
            connections.add(key)
            for rid,lid in ((c.from_road,c.from_lane),(c.to_road,c.to_lane)):
                if rid not in roads or lid not in {l.id for l in roads[rid].lanes}: raise ValueError('Connection references an unknown road/lane')
        for value in [*self.signals,*self.objects]:
            if value.road_id not in roads: raise ValueError('Signal/object references an unknown road')
            if value.s_m>sum(g.length_m for g in roads[value.road_id].geometries): raise ValueError('Signal/object is outside its road length')
        for signal in self.signals:
            if any(i not in {l.id for l in roads[signal.road_id].lanes} for i in signal.lane_ids): raise ValueError('Signal validity references an unknown lane')
        if self.read_only and not self.original_opendrive: raise ValueError('Read-only imports must retain original OpenDRIVE bytes')
        if self.unsupported and not self.read_only: raise ValueError('Unsupported OpenDRIVE requires read-only preservation')
        return self


class ObjectMapCreate(StrictModel):
    project_id: Identity = 'default'
    id: Identity
    expected_revision: int = Field(default=0, ge=0)
    definition: ObjectMapDefinition


class ObjectMapPreview(StrictModel):
    project_id: Identity = 'default'
    definition: ObjectMapDefinition


class ObjectMapImport(StrictModel):
    project_id: Identity = 'default'
    content: str = Field(min_length=1, max_length=20000000)
    name: Name = 'Imported road map'
    rights: Rights


class ObjectMapBuild(StrictModel):
    project_id: Identity = 'default'
    revision: int = Field(ge=1)
    sha256: Sha256
    expected_map_revision: int = Field(default=0, ge=0)
    server_version: str = Field(default='1ae5356', min_length=1, max_length=200, description='Requested compatible CARLA build; not a live availability probe')
