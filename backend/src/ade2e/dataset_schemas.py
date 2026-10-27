"""Reviewed detection annotations over immutable original-image identities."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .model_schemas import ModelLabel, ModelNote, Sha256
from .perception_schemas import PerceptionImage
from .schemas import Name, StrictModel

Split = Literal['train', 'validation', 'test', 'unspecified']
AnnotationId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')]
Coordinate = Annotated[float, Field(allow_inf_nan=False, strict=True)]


class DatasetImageCreate(StrictModel):
    recording_id: Name
    channel_id: int = Field(ge=0, le=65535, strict=True)
    ordinal: int = Field(ge=0, le=100000, strict=True)
    decoder_id: Name
    decoder_version: Name


class DatasetImageInput(DatasetImageCreate):
    recording_sha256: Sha256
    message_sha256: Sha256
    message_size_bytes: int = Field(ge=0, strict=True)
    log_time_ns: str
    publish_time_ns: str

    @field_validator('log_time_ns', 'publish_time_ns')
    @classmethod
    def exact_clock(cls, value):
        if not value.isascii() or not value.isdigit() or len(value) > 20 or not 0 <= int(value) <= 2**64-1 or str(int(value)) != value:
            raise ValueError('Capture clocks must be canonical uint64 decimal strings')
        return value


class DatasetImage(StrictModel):
    id: Name
    created_at: str
    input: DatasetImageInput
    image: PerceptionImage
    recording_snapshot: dict


class DatasetAnnotation(StrictModel):
    id: AnnotationId
    label: ModelLabel
    bbox_xyxy: tuple[Coordinate, Coordinate, Coordinate, Coordinate]
    iscrowd: bool = Field(default=False, strict=True)

    @field_validator('bbox_xyxy')
    @classmethod
    def valid_rectangle(cls, box):
        if not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]:
            raise ValueError('Annotations require nonnegative, positive-area pixel xyxy boxes')
        return box


class DatasetMember(StrictModel):
    image_id: Name
    split: Split = 'unspecified'
    review_status: Literal['unreviewed', 'reviewed'] = 'unreviewed'
    reviewer: str = Field(default='', max_length=180)
    review_note: str = Field(default='', max_length=4000)
    annotation_source: Literal['human', 'synthetic', 'imported', 'unknown'] = 'unknown'
    annotations: list[DatasetAnnotation] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def review_and_ids(self):
        if len({item.id for item in self.annotations}) != len(self.annotations):
            raise ValueError('Annotation IDs must be unique within each image')
        if self.review_status == 'reviewed' and (not self.reviewer.strip() or not self.review_note.strip() or self.annotation_source == 'unknown'):
            raise ValueError('Reviewed images need reviewer, review note and explicit annotation source; empty reviewed labels declare a negative image')
        return self


class DatasetCreate(StrictModel):
    name: Name
    description: str = Field(default='', max_length=4000)
    labels: list[ModelLabel] = Field(min_length=1, max_length=256)
    source: str = Field(default='unknown', max_length=2000)
    source_revision: str = Field(default='unknown', max_length=300)
    license: str = Field(default='unknown', max_length=1000)
    note: ModelNote
    images: list[DatasetMember] = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def taxonomy_and_members(self):
        if len(set(self.labels)) != len(self.labels):
            raise ValueError('Dataset labels must be unique after whitespace normalization')
        if len({item.image_id for item in self.images}) != len(self.images):
            raise ValueError('Dataset image IDs must be unique')
        if any(annotation.label not in self.labels for member in self.images for annotation in member.annotations):
            raise ValueError('Every annotation label must belong to the dataset taxonomy')
        return self


class DatasetRevisionCreate(DatasetCreate):
    expected_revision: int = Field(ge=1, le=2**31-1, strict=True)


class DatasetStats(StrictModel):
    images: int
    reviewed: int
    unreviewed: int
    annotations: int
    negative_images: int
    duplicate_image_groups: int
    splits: dict[Split, int]


class DatasetSnapshot(DatasetCreate):
    schema_version: Literal['1'] = '1'
    id: Name
    revision: int = Field(ge=1, le=2**31-1, strict=True)
    created_at: str
    label_map_hash: Sha256
    image_assets: list[DatasetImage]
    stats: DatasetStats


class DatasetDetail(DatasetSnapshot):
    manifest_hash: Sha256
    manifest_size_bytes: int
    download_url: str
