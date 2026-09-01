"""The stored tf-idf + LSI fit behind ranked ``graph search`` (spec section 22). Stdlib only."""

import json
import sqlite3

from pydantic import BaseModel, ConfigDict, Field

TEXT_MODEL_KIND = "tfidf_lsi"


class TextModel(BaseModel):
    """One build's naming fit, kept so a query can be ranked without refitting anything.

    ``projection`` carries the idf weights folded into the LSI components, so scoring a query is
    one row lookup per token and a matrix product; ``doc_vectors`` rows are already unit length,
    which is what makes that product a cosine.
    """

    model_config = ConfigDict(frozen=True)

    node_ids: tuple[str, ...] = ()
    vocabulary: dict[str, int] = Field(default_factory=dict)
    components: int = 0
    projection: bytes = b""
    doc_vectors: bytes = b""

    @property
    def usable(self) -> bool:
        """Whether this model has both a vocabulary to look a query up in and documents to rank."""
        return bool(self.node_ids) and bool(self.vocabulary) and self.components > 0

    def row(self) -> tuple[str, str, str, int, bytes, bytes]:
        """This model as the ``graph_text_model`` column tuple, minus the repo key."""
        return (
            TEXT_MODEL_KIND,
            json.dumps(list(self.node_ids)),
            json.dumps(self.vocabulary),
            self.components,
            self.projection,
            self.doc_vectors,
        )

    @classmethod
    def of_row(cls, row: sqlite3.Row) -> "TextModel":
        """Rebuild a model from its stored row, as ``sqlite3.Row`` hands it back."""
        return cls(
            node_ids=tuple(json.loads(row["node_ids"])),
            vocabulary=json.loads(row["vocabulary"]),
            components=row["components"],
            projection=row["projection"],
            doc_vectors=row["doc_vectors"],
        )
