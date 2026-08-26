"""What a runner talks to a model through (spec 9.3).

A leaf on purpose: `runner.py` and `sdk_client.py` are on opposite sides of the SDK boundary and
both need this name, so it lives below both and imports nothing from the package.
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol


class ClientSession(Protocol):
    """The four members of the SDK client a runner uses.

    A protocol rather than an ABC: the object is a third party's, and a test double must not have
    to inherit ours to stand in for it.
    """

    async def __aenter__(self) -> "ClientSession": ...

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any: ...

    async def query(self, prompt: str) -> None: ...

    #: a plain `def`: the SDK's own is an async-generator method, awaited by `async for`
    def receive_response(self) -> AsyncIterator[Any]: ...


#: builds the session one run talks through, from that run's options and its bound tools. The
#: arguments are the SDK runner's own models, which this module sits below and cannot name, so
#: only the answer is pinned: it is the half every caller consumes.
ClientFactory = Callable[..., ClientSession]
