"""Shared contract primitives without application-domain imports."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, StringConstraints

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
