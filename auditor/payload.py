"""The two shells every ``--json`` payload is built on, so a wire model declares only its fields.

Frozen in one place: a payload records what a command already decided, so a renderer that mutated
one would be editing the JSON the same model emits.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, RootModel

RowT = TypeVar("RowT", bound=BaseModel)


class WirePayload(BaseModel):
    """One command or query result as a frozen JSON object."""

    model_config = ConfigDict(frozen=True)


class WireRows(RootModel[tuple[RowT, ...]], Generic[RowT]):
    """A result whose wire shape is a JSON array, parameterised by the model of one row."""

    model_config = ConfigDict(frozen=True)
