"""Image appearance is not measured illumination, weather or driving coverage."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .base_schemas import Name, StrictModel
from .case_schemas import Identity
from .model_schemas import Sha256

Revision = Annotated[int, Field(strict=True, ge=1, le=2**31-1)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]
FeatureTag = Literal['dark_image', 'bright_image', 'low_contrast', 'high_contrast', 'highlight_rich']
Lighting = Literal['unknown', 'day', 'night', 'twilight']
Weather = Literal['unknown', 'dry', 'rain', 'snow', 'fog']


class SceneIndexSelection(StrictModel):
    collection_id: Identity
    collection_revision: Revision
    name: Name
    note: Note


class SceneIndexCreate(SceneIndexSelection):
    expected_preview_hash: Sha256
    confirm_local_analysis: bool = Field(strict=True)

    @model_validator(mode='after')
    def consent(self):
        if not self.confirm_local_analysis:
            raise ValueError('Explicit local image-analysis consent is required')
        return self


class SceneTagReview(StrictModel):
    expected_review_revision: int = Field(strict=True, ge=0, le=2**31-2)
    source_key: Sha256
    accepted_feature_tags: list[FeatureTag] = Field(default_factory=list, max_length=5)
    lighting: Lighting = 'unknown'
    weather: Weather = 'unknown'
    status: Literal['draft', 'reviewed'] = 'draft'
    reviewer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    note: Note

    @model_validator(mode='after')
    def unique_tags(self):
        if len(set(self.accepted_feature_tags)) != len(self.accepted_feature_tags):
            raise ValueError('Accepted tags cannot repeat')
        self.accepted_feature_tags.sort()
        return self


class SceneQuery(StrictModel):
    review_revision: int | None = Field(default=None, strict=True, ge=0, le=2**31-1)
    anchor_key: Sha256 | None = None
    feature_tag: FeatureTag | None = None
    feature_scope: Literal['proposed', 'reviewed'] = 'proposed'
    lighting: Literal['all', 'unknown', 'day', 'night', 'twilight'] = 'all'
    weather: Literal['all', 'unknown', 'dry', 'rain', 'snow', 'fog'] = 'all'
    review_status: Literal['all', 'pending', 'draft', 'reviewed'] = 'all'
    exclude_same_recording: bool = Field(default=False, strict=True)
    exclude_exact_pixels: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def anchor_filters(self):
        if (self.exclude_same_recording or self.exclude_exact_pixels) and self.anchor_key is None:
            raise ValueError('Recording/duplicate exclusion requires a query image')
        return self


class SceneCollectionCreate(StrictModel):
    query: SceneQuery
    expected_search_hash: Sha256
    selected_keys: list[Sha256] = Field(min_length=1, max_length=32)
    name: Name
    purpose: Note

    @model_validator(mode='after')
    def unique_selection(self):
        if self.query.review_revision is None:
            raise ValueError('Pin the tag-review revision from the search result')
        if len(set(self.selected_keys)) != len(self.selected_keys):
            raise ValueError('Select each result once; no repeated source keys')
        return self
