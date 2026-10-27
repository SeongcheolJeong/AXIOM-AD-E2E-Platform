"""Immutable model declarations; registration is not execution or license approval."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from .base_schemas import Name, StrictModel

MAX_MODEL_BYTES = 128 * 1024 * 1024
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ModelProfile = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")]
ModelLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
ModelNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ModelArtifact(StrictModel):
    id: Name
    name: Name
    sha256: Sha256
    size_bytes: int = Field(ge=1, le=MAX_MODEL_BYTES, strict=True)
    created_at: str
    download_url: str


class ModelCreate(StrictModel):
    name: Name
    description: str = Field(default="", max_length=4000)
    artifact_id: Name
    profile: ModelProfile
    input_width: int = Field(ge=32, le=1280, strict=True)
    input_height: int = Field(ge=32, le=1280, strict=True)
    labels: list[ModelLabel] = Field(min_length=1, max_length=256)
    source: str = Field(default="unknown", max_length=2000)
    source_revision: str = Field(default="unknown", max_length=300)
    weights_license: str = Field(default="unknown", max_length=300)
    weights_license_url: str = Field(default="", max_length=2000)
    code_license: str = Field(default="unknown", max_length=300)
    training_data_note: str = Field(default="unknown", max_length=4000)
    note: ModelNote

    @field_validator("labels")
    @classmethod
    def unique_labels(cls, labels: list[str]) -> list[str]:
        if len(labels) != len(set(labels)):
            raise ValueError("Labels must be unique after whitespace normalization; order defines class IDs")
        return labels


class ModelRevisionCreate(ModelCreate):
    expected_revision: int = Field(ge=1, le=2**31 - 1, strict=True)


class ModelSnapshot(ModelCreate):
    schema_version: Literal["1"] = "1"
    id: Name
    revision: int = Field(ge=1, le=2**31 - 1, strict=True)
    created_at: str
    artifact: ModelArtifact
    label_map_hash: Sha256


class ModelDetail(ModelSnapshot):
    manifest_hash: Sha256
    manifest_size_bytes: int = Field(ge=1, strict=True)
    download_url: str
