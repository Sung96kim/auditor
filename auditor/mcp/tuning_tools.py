# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface: JSON payloads by contract)
"""propose_tuning: the in-session knob producer (spec 9.2, 9.5, 11).

Its own module rather than a `refine_tools` sibling: spec 5.4 says a tuning proposal is not a
refinement, and the two surfaces share neither a run's staging nor a verifier.
"""

from fastmcp.exceptions import ToolError

from auditor.graph.payloads import TuningRowPayload
from auditor.graph.refine.models import ClientKind, ProducerKind
from auditor.graph.refine.service import RefinementRefused, RefinementService
from auditor.graph.refine.trial import TuningService
from auditor.graph.refine.tuning import TUNING_KNOBS, TuningRefused
from auditor.mcp.helpers import MUTATING_ONCE, ToolRepo, tool_repo, tool_user
from auditor.mcp.server import mcp


async def _tuning(repo: ToolRepo) -> TuningService:
    """One tuning service per call, over the run registry this process already shares."""
    user = await tool_user(repo)
    return TuningService(
        service=RefinementService(repo.index, repo.root, repo.settings, user)
    )


@mcp.tool(annotations=MUTATING_ONCE)
async def propose_tuning(
    key: str,
    value: str,
    reason: str,
    path: str = ".",
    client: str = "cli",
) -> dict:
    """Propose one graph knob change for this checkout. Records a `pending` row and applies
    nothing: a human runs `auditr graph tuning accept <id> --token <word>` after reading the
    trial. ``key`` must be allow-listed and only ``stopwords`` is shipped, so ``value`` is one
    lowercase token to stop treating as meaningful in concept names; the three numeric knobs are
    declared and refused, because measured on real repos they are either inert or move the cluster
    count by more than 100 percent. ``reason`` is required and is what the human reads.
    One proposal per key per day; a second proposal for a token already pending supersedes it, and
    a token already active is an error. Returns the row {tuning_id, key, value, status, token,
    reason, run_id, created_at, allow_list}; ``token`` is the confirmation word the accept has to
    repeat. The trial that measures it is a facts-only rebuild the observer runs, or
    `auditr graph tuning measure <id>` when no daemon is attached, and it needs a built graph:
    on a checkout with none the trial records that and refuses once."""
    async with tool_repo(path) as repo:
        # narrowly, and around this line alone: `ValidationError` is a `ValueError`, so a whole
        # `try` here would report a model that stopped validating as a bad client (S11 L3)
        try:
            kind = ClientKind(client)
        except ValueError as exc:
            raise ToolError(f"unknown client: {client}") from exc
        try:
            tuning = await _tuning(repo)
            row = await tuning.propose(
                key, value, reason, producer=ProducerKind.AGENT, client=kind
            )
        except (TuningRefused, RefinementRefused) as exc:
            raise ToolError(str(exc)) from exc
        payload = TuningRowPayload.of(row)
    return {
        "tuning_id": payload.tuning_id,
        "key": payload.key,
        # the decoded token, through the one decoder the CLI and the live page also read: an
        # agent that echoed `value_json` back told the user to accept `"helper"` with the quotes
        "value": payload.value,
        "status": payload.status.value,
        "token": payload.token,
        "reason": payload.reason,
        "run_id": payload.run_id,
        "created_at": payload.created_at,
        "allow_list": sorted(k for k, v in TUNING_KNOBS.items() if v.shipped),
    }
