"""External map bytes and preparation are distinct from editable Object Maps."""
from typing import Any, Literal
from pydantic import Field
from .base_schemas import Name, StrictModel
from .scenario_v2_schemas import Identity, CatalogReference
from .simulation_catalog_schemas import Rights, Sha256

MAX_UPLOAD_BYTES = 32 * 1024 * 1024


class MapSourceAttachment(StrictModel):
    filename: str = Field(min_length=1, max_length=180)
    role: str = Field(min_length=1, max_length=100)
    content_base64: str = Field(min_length=1, max_length=44739244)


class MapSourceCreate(StrictModel):
    project_id: Identity = 'default'
    id: Identity | None = None
    expected_revision: int = Field(default=0, ge=0)
    name: Name
    source_kind: Literal['opendrive', 'ngii_shp']
    filename: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(min_length=1, max_length=44739244)
    rights: Rights
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[MapSourceAttachment] = Field(default_factory=list, max_length=8)


class MapSourceAction(StrictModel):
    project_id: Identity = 'default'
    revision: int = Field(ge=1)
    sha256: Sha256


class MapSourceConvert(MapSourceAction):
    options: dict[str, Any] = Field(default_factory=dict)


class MapSourceQualify(MapSourceAction):
    conversion_ref: CatalogReference
    server_version: str = Field(default='1ae5356', min_length=1, max_length=200)


class MapSourcePublish(MapSourceAction):
    conversion_ref: CatalogReference
    execution_id: Identity
    name: Name | None = None
