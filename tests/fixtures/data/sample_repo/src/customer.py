"""Customer domain — duplicates account.py's model + function shape (cross-file dedup)."""

from pydantic import BaseModel


class Party(BaseModel):
    name: str
    email: str
    phone: str
    country: str


def canonicalize(raw: dict) -> dict:
    cleaned = {k: v for k, v in raw.items() if v is not None}
    cleaned["email"] = cleaned.get("email", "").lower()
    return cleaned
