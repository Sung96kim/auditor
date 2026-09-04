"""What a model-driven run is told and what it may answer with (spec 9.4).

Module-level constants only, so the SDK-free runner, the client factory and the brief all read one
text. Nothing here imports the rest of the package.
"""

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SYSTEM_PROMPT = """\
You are refining a code graph. Every fact in the graph came from the source; your job is to place
the few facts the deterministic resolver could not place, and to say so when you cannot place one.

Rules:
1. Evidence only. Propose a correction when you have read the source that proves it. If you have
   not read the call site and the definition, you do not have the evidence.
2. Prefer the narrowest action the row admits. A row with candidates wants resolve_ambiguous. A row
   whose definers list already holds the right node wants confirm_edge or add_edge. Reach for
   annotate_node only when no edge answers the row.
3. Use unresolvable when the call is dynamic dispatch with no literal call site: a name looked up
   at run time, a registry, or a receiver assembled from data. That is a real answer, not a
   failure.
4. Never propose an edge for a receiver whose type you cannot see. If the receiver is a parameter
   with no annotation and no local assignment, the type is not visible and the row is unresolvable.
5. One proposal per target at most. Two proposals for the same name contradict each other and both
   are refused.
6. Stop when you are unsure. A row left alone stays in the queue for the next run; a wrong
   correction has to be found and reverted by a human.

Tools:
- Read, Grep and Glob read the checkout. Use them before every proposal.
- mcp__graph__propose takes one proposal and returns the verdict: whether it was staged or
  rejected, the tier it earned, and, when it was refused, exactly what the check found. A rejected
  proposal is recorded; do not retry the same one.
- mcp__graph__brief re-reads your brief with the verdicts you have earned so far.

You cannot commit. The runner commits what you staged when you finish.

Finish by answering with the JSON the output schema asks for: a one-line summary, how many
proposals you made, and why you stopped.
"""

SYSTEM_PROMPT_SHA: str = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


class RunAnswer(BaseModel):
    """The structured answer a run ends with, so a run that produced nothing says why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(max_length=280)
    proposed: int = Field(ge=0)
    stopped_because: Literal["done", "unsure", "budget", "nothing_to_do"]


RUN_ANSWER_SCHEMA: dict[str, Any] = RunAnswer.model_json_schema()
#: both keys, and `schema` non-None: anything else drops `--json-schema` silently (spike A.6)
OUTPUT_FORMAT: dict[str, Any] = {"type": "json_schema", "schema": RUN_ANSWER_SCHEMA}

#: the checkout-reading tools a run may use (Invariant 4)
MODEL_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")
GRAPH_SERVER = "graph"
GRAPH_TOOLS: tuple[str, ...] = ("propose", "brief")
#: injected by `--json-schema`, called once to deliver the answer, and counted in `num_turns`
STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
ALLOWED_TOOLS: tuple[str, ...] = (
    *MODEL_TOOLS,
    *(f"mcp__{GRAPH_SERVER}__{name}" for name in GRAPH_TOOLS),
    STRUCTURED_OUTPUT_TOOL,
)

PROPOSE_DESCRIPTION = (
    "Propose one correction to the graph and get the verdict back. The argument is the nested "
    "proposal shape: kind, target (src, dst, edge_kind, node_id, name, members), payload (label, "
    "annotation, candidate, reason_code, call_form), reason, evidence, confidence. The verdict "
    "says whether it was staged or rejected and why; a rejected proposal is recorded, so do not "
    "retry it."
)
BRIEF_DESCRIPTION = "Re-read this run's brief, with the verdicts earned so far appended. Takes no arguments."
