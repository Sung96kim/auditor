"""A fully clean, well-typed module — the negative baseline. Should produce zero findings
even under the strict profile."""

from collections.abc import Iterable

from pydantic import BaseModel


class Money(BaseModel):
    amount: int
    currency: str


def total(items: Iterable[Money]) -> Money:
    by_currency: dict[str, int] = {}
    for item in items:
        by_currency[item.currency] = by_currency.get(item.currency, 0) + item.amount
    only = next(iter(by_currency.items()), ("USD", 0))
    return Money(amount=only[1], currency=only[0])
