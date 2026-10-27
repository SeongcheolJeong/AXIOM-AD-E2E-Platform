"""An investigation is a reviewed workflow object, never a driving verdict."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .base_schemas import StrictModel

Identity = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_-]{1,100}$')]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
Nanoseconds = Annotated[str, StringConstraints(pattern=r'^(0|[1-9][0-9]{0,19})$')]
CaseState = Literal['open', 'investigating', 'resolved', 'dismissed']


class RecordingSelection(StrictModel):
    kind: Literal['recording'] = 'recording'
    recording_id: Identity
    channel_id: int = Field(ge=0, le=65535, strict=True)
    start_time_ns: Nanoseconds
    end_time_ns: Nanoseconds

    @model_validator(mode='after')
    def interval(self):
        if not 0 <= int(self.start_time_ns) <= int(self.end_time_ns) < 2**64:
            raise ValueError('Use an inclusive, ordered uint64 log-time interval')
        return self


class EvaluationSelection(StrictModel):
    kind: Literal['evaluation'] = 'evaluation'
    evaluation_id: Identity
    image_id: Identity


class CaseFields(StrictModel):
    title: Text
    note: str = Field(min_length=1, max_length=4000)
    assignee: str = Field(default='', max_length=180)

    @field_validator('note', 'assignee')
    @classmethod
    def clean_text(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def note_present(self):
        if not self.note:
            raise ValueError('An investigation note is required')
        return self


class CaseCreate(CaseFields):
    source: Annotated[RecordingSelection | EvaluationSelection, Field(discriminator='kind')]


class CaseRevisionCreate(CaseFields):
    expected_revision: int = Field(ge=1, strict=True)
    state: CaseState = 'open'
    disposition_note: str = Field(default='', max_length=4000)
    add_evaluation: EvaluationSelection | None = None

    @model_validator(mode='after')
    def disposition(self):
        self.disposition_note = self.disposition_note.strip()
        if self.state in ('resolved', 'dismissed') and not self.disposition_note:
            raise ValueError('Resolved/dismissed cases require a disposition note; this is not a test pass')
        return self
