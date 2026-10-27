"""Engine metadata is inert data; no engine SDK imports belong here."""
import hashlib
from typing import Annotated, Literal
from pydantic import Field, StringConstraints, model_validator, model_serializer
from .base_schemas import Name, StrictModel
from .scenario_v2_schemas import Identity, CityPose, Dimensions, CatalogReference

Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
NativeId = Annotated[str, StringConstraints(min_length=1, max_length=160)]

class Rights(StrictModel):
    license: str = Field(min_length=1, max_length=500)
    source_url: str = Field(default='', max_length=2000)
    redistribution: Literal['unknown','permitted','restricted'] = 'unknown'
    notes: str = Field(default='', max_length=4000)

class OpenDriveContent(StrictModel):
    content: str = Field(min_length=1, max_length=20000000)
    sha256: Sha256

    @model_validator(mode='after')
    def exact_bytes(self):
        if hashlib.sha256(self.content.encode('utf-8')).hexdigest() != self.sha256:
            raise ValueError('OpenDRIVE content SHA-256 does not match its original UTF-8 bytes')
        return self

class LaneNode(CityPose):
    id: NativeId
    road_id: int
    section_id: int
    lane_id: int
    lane_width_m: float = Field(gt=0, le=30)
    is_junction: bool = False
    next_ids: list[NativeId] = Field(default_factory=list, max_length=32)
    left_id: NativeId | None = None
    right_id: NativeId | None = None
    lane_change_left: bool = False
    lane_change_right: bool = False
    closed: bool = False
    speed_limit_mps: float | None = Field(default=None, gt=0, le=60)
    priority: int | None = Field(default=None, ge=0, le=100)
    conflict_ids: list[NativeId] = Field(default_factory=list, max_length=64)

    @model_serializer(mode='wrap')
    def preserve_legacy_hash(self, serializer):
        value=serializer(self)
        for key, default in {'left_id':None,'right_id':None,'lane_change_left':False,
            'lane_change_right':False,'closed':False,'speed_limit_mps':None,'priority':None,'conflict_ids':[]}.items():
            if value.get(key)==default:value.pop(key,None)
        return value

class GeneratedMap(StrictModel):
    source: CatalogReference
    compiler_id: str = Field(min_length=1, max_length=100)
    compiler_version: str = Field(min_length=1, max_length=100)
    compiler_sha256: Sha256 | None = None
    source_schema: Literal['object-map/1'] = 'object-map/1'
    build_hash: Sha256
    road_ids: dict[str,int] = Field(default_factory=dict, max_length=1000)
    environment: Literal['procedural_roads_only'] = 'procedural_roads_only'

class ExternalMap(StrictModel):
    """Qualified immutable external roads, never attributed to authored compiler."""
    source: CatalogReference
    conversion: CatalogReference
    source_schema: Literal['map-source/1'] = 'map-source/1'
    qualification_execution_id: Identity
    qualification_manifest_hash: Sha256
    input_opendrive_sha256: Sha256
    native_opendrive_sha256: Sha256
    coordinate_transform: dict = Field(default_factory=dict)
    environment: Literal['procedural_roads_only'] = 'procedural_roads_only'
    qualification_scope: Literal['native_map_load_readback_not_driving'] = 'native_map_load_readback_not_driving'

class Signal(StrictModel):
    id: NativeId
    pose: CityPose
    kind: Literal['traffic_light','stop','other']
    lanelet_ids: list[int] = Field(default_factory=list, max_length=32)
    node_ids: list[NativeId] = Field(default_factory=list, max_length=5000)
    regulatory_kind: Literal['stop','yield','traffic_light'] | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_hash(self, serializer):
        value=serializer(self)
        if not self.node_ids:value.pop('node_ids',None)
        if self.regulatory_kind is None:value.pop('regulatory_kind',None)
        return value

class MapDefinition(StrictModel):
    resource_kind: Literal['map'] = 'map'
    name: Name
    engine: Literal['carla'] = 'carla'
    native_name: str = Field(min_length=1, max_length=200)
    server_version: str = Field(min_length=1, max_length=200)
    coordinate_system: Literal['carla_world_m_x_forward_y_right_z_up'] = 'carla_world_m_x_forward_y_right_z_up'
    opendrive: OpenDriveContent
    nodes: list[LaneNode] = Field(min_length=1, max_length=50000)
    spawn_points: list[CityPose] = Field(default_factory=list, max_length=5000)
    signals: list[Signal] = Field(default_factory=list, max_length=5000)
    rights: Rights
    generated: GeneratedMap | None = None
    external: ExternalMap | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_hash(self, serializer):
        value=serializer(self)
        if self.generated is None:value.pop('generated',None)
        if self.external is None:value.pop('external',None)
        return value

    @model_validator(mode='after')
    def graph_links(self):
        if self.generated is not None and self.external is not None:
            raise ValueError('A map cannot claim both authored and external provenance')
        if self.external and (self.external.input_opendrive_sha256 != self.opendrive.sha256 or
                self.external.native_opendrive_sha256 != self.opendrive.sha256):
            raise ValueError('External map execution and native readback hashes differ')
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError('Native lane graph node IDs must be unique')
        for node in self.nodes:
            if len(node.next_ids) != len(set(node.next_ids)) or any(identity not in ids for identity in node.next_ids):
                raise ValueError('Native lane graph has duplicate or unresolved successors')
            if any(identity is not None and identity not in ids for identity in (node.left_id,node.right_id)):
                raise ValueError('Lane adjacency references an unknown node')
            if (node.lane_change_left and node.left_id is None) or (node.lane_change_right and node.right_id is None):
                raise ValueError('Lane change permission requires an adjacent node')
        if len({s.id for s in self.signals}) != len(self.signals):
            raise ValueError('Signal IDs must be unique')
        if any(identity not in ids for signal in self.signals for identity in signal.node_ids):
            raise ValueError('Signal stop-line binding references an unknown lane node')
        return self

class AssetDefinition(StrictModel):
    resource_kind: Literal['asset'] = 'asset'
    name: Name
    engine: Literal['carla'] = 'carla'
    blueprint_id: str = Field(min_length=1, max_length=240)
    actor_kind: Literal['vehicle','pedestrian','static']
    dimensions: Dimensions | None = None
    attributes: dict[str,list[str]] = Field(default_factory=dict, max_length=100)
    thumbnail_uri: str | None = Field(default=None, max_length=2000)
    rights: Rights

class ControllerDefinition(StrictModel):
    resource_kind: Literal['controller'] = 'controller'
    name: Name
    adapter_id: Identity
    adapter_version: str = Field(min_length=1, max_length=100)
    mode: Literal['route_follow','traffic','timed_replay','autoware','static','reactive']
    status: Literal['available','not_configured'] = 'not_configured'
    description: str = Field(default='', max_length=4000)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    image_digest: str | None = Field(default=None, max_length=300)
    rights: Rights

class CatalogCreate(StrictModel):
    project_id: Identity = 'default'
    id: Identity
    expected_revision: int = Field(default=0, ge=0)
    definition: Annotated[MapDefinition | AssetDefinition | ControllerDefinition, Field(discriminator='resource_kind')]

class RouteRequest(StrictModel):
    project_id: Identity = 'default'
    revision: int = Field(ge=1)
    sha256: Sha256
    start: CityPose
    goal: CityPose
    via: list[CityPose] = Field(default_factory=list, max_length=32)
    max_snap_distance_m: float = Field(default=5, gt=0, le=10)
