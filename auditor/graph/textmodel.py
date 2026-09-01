"""The stored tf-idf + LSI fit behind ranked ``graph search`` (spec section 22). Stdlib only."""

import json
import sqlite3
import struct

from pydantic import BaseModel, ConfigDict, Field

TEXT_MODEL_KIND = "tfidf_lsi"
#: the one dtype name the fit writes and the query reads back; numpy stays out of this module
TEXT_MODEL_DTYPE = "float32"
#: bytes per stored value; "f" is the struct code for the dtype above, so neither can drift
TEXT_MODEL_ITEMSIZE = struct.calcsize("f")


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
        """Whether a query can be scored against this fit at all.

        Both blob lengths are checked against the shape the other fields declare, so a torn cache
        row answers False here rather than raising out of the reshape at query time.
        """
        if not (self.node_ids and self.vocabulary and self.components > 0):
            return False
        row_bytes = TEXT_MODEL_ITEMSIZE * self.components
        return len(self.projection) == row_bytes * len(self.vocabulary) and len(
            self.doc_vectors
        ) == row_bytes * len(self.node_ids)

    def values(self) -> dict[str, str | int | bytes]:
        """This model as ``graph_text_model`` column values, minus the repo key.

        A mapping rather than a tuple so :meth:`BaseDB.insert_sql` orders it from the table
        declaration and a reordered column cannot write a transposed row.
        """
        return {
            "kind": TEXT_MODEL_KIND,
            "node_ids": json.dumps(list(self.node_ids)),
            "vocabulary": json.dumps(self.vocabulary),
            "components": self.components,
            "projection": self.projection,
            "doc_vectors": self.doc_vectors,
        }

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
