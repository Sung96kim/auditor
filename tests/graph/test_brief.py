"""The brief a refinement run is given: what it shows, what it caps, and what it records."""

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from auditor.graph.model import EdgeKind
from auditor.graph.refine.brief import BriefBuilder
from auditor.graph.refine.models import (
    Proposal,
    Refinement,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
    Tier,
)
from auditor.graph.refine.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_SHA, RunAnswer
from auditor.graph.refine.service import RefinementRefused

GOLDEN = Path(__file__).parent / "fixtures" / "brief_golden.txt"
#: escaped, so this file is itself free of the character it forbids
EM_DASH = "\u2014"
#: the one queue row in `refine_service` a proposal can actually answer
QUEUED_CALL = "impl.py::Impl.run"
CALL_EDGE = Proposal(
    kind=RefinementKind.ADD_EDGE,
    target=RefinementTarget(
        src=QUEUED_CALL,
        dst="svc.py::load_user",
        edge_kind=EdgeKind.CALLS,
        name="load_user",
    ),
    reason="Impl.run calls load_user, which svc.py defines",
)


def _builder(service, **limits) -> BriefBuilder:
    """A builder over the service's own reader, optionally under tightened limits."""
    return BriefBuilder(
        facts=service.facts,
        limits=service.user.observer.limits.model_copy(update=limits),
    )


async def _stale_row(service, *, src: str, dst: str, status: RefinementStatus, **kw):
    """One stored correction, so a brief has something to warn about."""
    run = await service.begin()
    return await service.index.refinements.add_refinement(
        Refinement(
            run_id=run.run_id,
            repo_identity=service.identity,
            kind=RefinementKind.ADD_EDGE,
            reason="it resolves there",
            target=RefinementTarget(
                src=src, dst=dst, edge_kind=EdgeKind.CALLS, name="load_user"
            ),
            tier=Tier.C,
            status=status,
            **kw,
        )
    )


async def test_a_target_carries_the_facts_the_verifier_will_check(refine_service):
    """The model must see the fact tuple its proposal is judged against, not just the name."""
    brief = await _builder(refine_service).build("")
    target = next(t for t in brief.targets if t.node_id == QUEUED_CALL)
    assert target.name == "load_user"
    assert "load_user" in target.facts
    assert (target.path, target.line) == ("impl.py", 3)


async def test_the_definers_are_the_queues_role_filtered_set(refine_service):
    brief = await _builder(refine_service).build("")
    target = next(t for t in brief.targets if t.node_id == QUEUED_CALL)
    (row,) = [
        r
        for r in await refine_service.index.graph.unresolved(node_ids=[QUEUED_CALL])
        if r["name"] == "load_user"
    ]
    assert list(target.definers) == row["definers"]
    assert list(target.candidates) == row["candidates"]


async def test_a_row_with_definers_admits_add_edge_and_a_node_row_does_not(
    refine_service,
):
    brief = await _builder(refine_service).build("")
    call = next(t for t in brief.targets if t.node_id == QUEUED_CALL)
    node_row = next(t for t in brief.targets if not t.definers)
    assert RefinementKind.ADD_EDGE in call.allowed
    assert RefinementKind.ADD_EDGE not in node_row.allowed
    assert RefinementKind.UNRESOLVABLE in node_row.allowed


async def test_the_cap_bounds_the_targets_and_the_total_says_what_it_left(
    refine_service,
):
    brief = await _builder(refine_service, max_nodes_per_run=1).build("")
    assert len(brief.targets) == 1
    assert brief.queue_total > 1
    assert brief.limits.max_targets == 1


async def test_only_the_capped_rows_are_read_from_the_queue(
    refine_service, monkeypatch
):
    """Decoding the whole queue for a number costs the same as briefing it (spec 5.6 sizes it in
    the thousands), so the cap has to reach the reader."""
    seen: list[int | None] = []
    original = refine_service.index.graph.unresolved

    async def spy(*args, **kwargs):
        seen.append(kwargs.get("limit"))
        return await original(*args, **kwargs)

    monkeypatch.setattr(refine_service.index.graph, "unresolved", spy)
    await _builder(refine_service, max_nodes_per_run=2).build("")
    assert seen == [2]


@pytest.mark.parametrize("scope", ["", "."])
async def test_an_empty_scope_and_a_dot_both_mean_the_whole_repo(refine_service, scope):
    brief = await _builder(refine_service).build(scope)
    assert brief.scope == ""
    assert {t.node_id for t in brief.targets} > {QUEUED_CALL}


async def test_a_scope_narrows_the_targets_to_what_is_under_it(refine_service):
    brief = await _builder(refine_service).build("impl.py")
    assert brief.targets
    assert all(t.node_id.startswith("impl.py") for t in brief.targets)
    assert brief.queue_total == len(brief.targets)


async def test_a_scope_outside_the_checkout_is_refused(refine_service):
    with pytest.raises(ValueError, match="not a repo-relative path"):
        await _builder(refine_service).build("../elsewhere")


async def test_a_stale_correction_under_the_scope_is_shown(refine_service):
    refinement_id = await _stale_row(
        refine_service,
        src=QUEUED_CALL,
        dst="svc.py::load_user",
        status=RefinementStatus.STALE,
    )
    brief = await _builder(refine_service).build("")
    assert [note.refinement_id for note in brief.stale] == [refinement_id]
    assert brief.stale[0].target == f"{QUEUED_CALL} -> svc.py::load_user"


@pytest.mark.parametrize("scope", ["impl.py", "svc.py"])
async def test_a_stale_correction_this_run_could_not_make_is_not_shown(
    refine_service, scope
):
    """The rule `StagedRun.covers` applies: a scope holding one end of the edge could not propose
    it, so warning about it is noise."""
    await _stale_row(
        refine_service,
        src=QUEUED_CALL,
        dst="svc.py::load_user",
        status=RefinementStatus.STALE,
    )
    assert (await _builder(refine_service).build(scope)).stale == ()


async def test_a_pinned_correction_that_drifted_is_shown_too(refine_service):
    """`pinned` is never auto-staled, only marked drifted (spec 5.7); both mean do not repeat it."""
    await _stale_row(
        refine_service,
        src=QUEUED_CALL,
        dst="svc.py::load_user",
        status=RefinementStatus.PINNED,
        drifted=True,
    )
    brief = await _builder(refine_service).build("")
    assert [note.status for note in brief.stale] == [RefinementStatus.PINNED]


async def test_a_pinned_correction_that_did_not_drift_is_left_alone(refine_service):
    await _stale_row(
        refine_service,
        src=QUEUED_CALL,
        dst="svc.py::load_user",
        status=RefinementStatus.PINNED,
    )
    assert (await _builder(refine_service).build("")).stale == ()


async def test_the_rendered_brief_matches_the_golden_file(refine_service):
    """The template is a pin (spec 21): regenerating this file is a deliberate edit."""
    brief = await _builder(refine_service).build("")
    assert brief.render() == GOLDEN.read_text(encoding="utf-8")


async def test_the_rendered_brief_stays_inside_its_own_line_budget(refine_service):
    rendered = (await _builder(refine_service).build("")).render()
    assert EM_DASH not in rendered
    assert max(len(line) for line in rendered.splitlines()) <= 100


async def test_a_scope_with_no_queue_rows_says_so(refine_service):
    rendered = (await _builder(refine_service).build("base.py::Nothing")).render()
    assert "none: nothing under this scope is unresolved." in rendered


def test_the_system_prompt_hash_is_the_hash_of_the_system_prompt():
    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() == SYSTEM_PROMPT_SHA
    assert len(SYSTEM_PROMPT_SHA) == 64
    assert EM_DASH not in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "payload",
    [
        {"summary": "s", "proposed": 1, "stopped_because": "done", "extra": 1},
        {"summary": "s", "proposed": 1, "stopped_because": "finished"},
        {"summary": "s", "proposed": -1, "stopped_because": "done"},
    ],
)
def test_the_run_answer_refuses_a_shape_it_did_not_ask_for(payload):
    with pytest.raises(ValidationError):
        RunAnswer.model_validate(payload)


async def test_service_brief_records_the_prompt_on_the_run_row(refine_service):
    """Invariant 2: the run must show what it was asked, even if it dies before committing."""
    run = await refine_service.begin()
    brief = await refine_service.brief(run.run_id)
    stored = await refine_service.index.runs.run(run.run_id)
    assert stored.prompt == brief.render()
    assert stored.system_prompt_sha == SYSTEM_PROMPT_SHA


async def test_service_brief_refuses_a_run_it_did_not_open(refine_service):
    with pytest.raises(RefinementRefused, match="not open in this process"):
        await refine_service.brief("no-such-run")


async def test_service_brief_starts_with_no_verdicts_and_lists_them_after(
    refine_service,
):
    run = await refine_service.begin()
    assert (await refine_service.brief(run.run_id)).staged == ()
    verdict = await refine_service.propose(run.run_id, CALL_EDGE)
    again = await refine_service.brief(run.run_id)
    assert again.staged == (verdict,)
    assert verdict.outcome.value in again.render()


async def test_a_service_brief_is_scoped_to_the_run(refine_service):
    run = await refine_service.begin(scope="impl.py")
    brief = await refine_service.brief(run.run_id)
    assert brief.scope == "impl.py"
    assert all(t.node_id.startswith("impl.py") for t in brief.targets)
