"""Explicit curation; no inferred labels, implicit exclusions or execution."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .base_schemas import Name, StrictModel
from .case_schemas import Identity
from .dataset_schemas import Split
from .model_schemas import ModelLabel, Sha256

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]
Revision = Annotated[int, Field(strict=True, ge=1, le=2**31-1)]


class CaseSource(StrictModel):
    kind: Literal['case']
    case_id: Identity
    revision: Revision
    reason: Reason


class DatasetSource(StrictModel):
    kind: Literal['dataset']
    dataset_id: Identity
    revision: Revision
    split: Literal['all', 'train', 'validation', 'test', 'unspecified'] = 'all'
    reason: Reason


class ImageSource(StrictModel):
    kind: Literal['image']
    image_id: Identity
    reason: Reason


Source = Annotated[CaseSource | DatasetSource | ImageSource, Field(discriminator='kind')]


class CollectionDefinition(StrictModel):
    name: Name
    purpose: Reason
    sources: list[Source] = Field(min_length=1, max_length=32)


class CollectionCreate(CollectionDefinition):
    expected_preview_hash: Sha256


class CollectionRevise(CollectionCreate):
    expected_revision: Revision


class MemberDecision(StrictModel):
    key: Sha256
    action: Literal['include', 'exclude']
    image_id: Identity | None = None
    label_option_id: Sha256 | None = None
    split: Split = 'unspecified'
    reason: str = Field(default='', max_length=1500)

    @model_validator(mode='after')
    def explicit_choice(self):
        self.reason = self.reason.strip()
        if self.action == 'exclude':
            if not self.reason or self.image_id is not None or self.label_option_id is not None or self.split != 'unspecified':
                raise ValueError('Exclusions require a reason and no image, label or split assignment')
        elif self.image_id is None:
            raise ValueError('Every included source needs an explicitly selected captured image')
        return self


class CollectionDatasetSelection(StrictModel):
    collection_revision: Revision
    name: Name
    note: Reason
    labels: list[ModelLabel] = Field(min_length=1, max_length=256)
    decisions: list[MemberDecision] = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.labels)) != len(self.labels):
            raise ValueError('Target labels must be unique')
        if len({d.key for d in self.decisions}) != len(self.decisions):
            raise ValueError('One include/exclude decision is required per original source, without duplicates')
        return self


class CollectionDatasetCreate(CollectionDatasetSelection):
    expected_preview_hash: Sha256
