"""What a runner talks to a model through (spec 9.3).

A leaf on purpose: `runner.py` and `sdk_client.py` are on opposite sides of the SDK boundary and
both need this name, so it lives below both and imports nothing from the package.
"""

from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


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


class CodexThread(Protocol):
    """The one member of a Codex thread a runner uses."""

    async def run(self, prompt: str) -> Any: ...


class ServerStatus(BaseModel):
    """One mcp server the binary loaded, with as much identity as a refusal needs.

    ``handshake`` is the `serverInfo.version` the server answered with, which is how a run tells
    its own loopback shim from another concurrent run's; ``None`` means it never connected.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    handshake: str | None = None


class CodexSession(Protocol):
    """The four members of the Codex client a runner uses.

    Two of them are RPCs the SDK gives no wrapper for, so the private `.request` call and its
    response models live behind this seam rather than in the runner.
    """

    #: what this session's own shim answers with, so a refusal can compare the two
    handshake: str

    async def __aenter__(self) -> "CodexSession": ...

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any: ...

    async def thread_start(self, options: Any) -> CodexThread: ...

    #: every mcp server the binary loaded, from `mcpServerStatus/list`
    async def servers(self) -> tuple[ServerStatus, ...]: ...

    #: the account's primary rate limit window, or `None` when it reported none
    async def rate_limit(self) -> Any: ...


#: builds the Codex session one run talks through, from that run's options and its bound tools.
#: The same two arguments `ClientFactory` takes, because the Codex factory does the same job: it
#: is what stands the `graph` server up, on loopback rather than in process.
CodexFactory = Callable[..., CodexSession]
