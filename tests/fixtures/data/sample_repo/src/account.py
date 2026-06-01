"""Account domain. Its Contact model + normalize() intentionally share their shape with
customer.py, so the cross-file pass flags the duplication (within the production role)."""

from pydantic import BaseModel


class Contact(BaseModel):
    name: str
    email: str
    phone: str
    country: str


def normalize(raw: dict) -> dict:
    cleaned = {k: v for k, v in raw.items() if v is not None}
    cleaned["email"] = cleaned.get("email", "").lower()
    return cleaned
