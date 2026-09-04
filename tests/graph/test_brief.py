"""The brief a refinement run is given: what it shows, what it caps, and what it records."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auditor.graph.model import (
    QUEUE_ID_CAP,
    CallForm,
    EdgeKind,
    FactKind,
    UnresolvedReason,
    UnresolvedRow,
)
from auditor.graph.refine.brief import (
    LINE_WIDTH,
    Brief,
    BriefLimits,
    BriefTarget,
    StaleNote,
)
from auditor.graph.refine.facts import BriefBuilder
from auditor.graph.refine.models import (
    Proposal,
    ProposalOutcome,
    Refinement,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
    Tier,
    Verdict,
)
from auditor.graph.refine.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_SHA, RunAnswer
from auditor.graph.refine.service import (
    RefinementRefused,
    RefinementService,
    RunRegistry,
)
from auditor.graph.refine.verify import VerifyStatus

GOLDEN = Path(__file__).parent / "fixtures" / "brief_golden.txt"
#: escaped, so this file is itself free of the character it forbids
EM_DASH = "\u2014"
#: a node id past the line budget on its own, which no fixture repo produces and this repo has
LONG_ID = f"auditor/graph/refine/{'very_long_module_name_' * 4}service.py::Klass.method"
STAGED = Verdict(
    outcome=ProposalOutcome.STAGED,
    kind=RefinementKind.ADD_EDGE,
    tier=Tier.B,
    status=RefinementStatus.PENDING,
    detail="the call site and the definition both read",
)
#: the brief the golden file pins: its own targets, so a queue row elsewhere cannot move it
GOLDEN_BRIEF = Brief(
    scope="auditor/graph",
    commit_sha="0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c",
    queue_total=9,
    limits=BriefLimits(max_changes=25, max_targets=12),
    targets=(
        BriefTarget(
            node_id="auditor/graph/refine/service.py::RefinementService.commit",
            name="load_user",
            path="auditor/graph/refine/service.py",
            line=42,
            reason=UnresolvedReason.UNIMPORTABLE_NAME,
            fact_kind=FactKind.CALLEE,
            definers=("auditor/graph/svc.py::load_user",),
            facts=("commit", "load_user"),
        ),
        BriefTarget(
            node_id="auditor/graph/refine/overlay.py::Overlay.apply",
            name="apply",
            path="auditor/graph/refine/overlay.py",
            line=88,
            reason=UnresolvedReason.AMBIGUOUS_NAME,
            fact_kind=FactKind.ATTR_CALLEE,
            call_form=CallForm.ATTR,
            receiver_root="overlay",
            candidates=("auditor/graph/a.py::apply", "auditor/graph/b.py::apply"),
            definers=("auditor/graph/a.py::apply", "auditor/graph/b.py::apply"),
            resolution_path=("auditor/graph/refine/overlay.py",),
        ),
        BriefTarget(
            node_id="auditor/graph/svc.py::Base",
            name="Base",
            path="auditor/graph/svc.py",
            line=1,
            reason=UnresolvedReason.TEXT_SPARSE,
            fact_kind=FactKind.NODE,
        ),
        BriefTarget(
            node_id="auditor/graph/svc.py::load_user",
            name="get",
            path="auditor/graph/svc.py",
            line=7,
            reason=UnresolvedReason.SINGLETON_CLUSTER,
            fact_kind=FactKind.NODE,
        ),
    ),
    stale=(
        StaleNote(
            refinement_id=7,
            kind=RefinementKind.ADD_EDGE,
            status=RefinementStatus.STALE,
            target="auditor/graph/a.py::main -> auditor/graph/b.py::read_event",
        ),
    ),
    staged=(STAGED,),
)
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


def _builder(service: RefinementService, **limits: Any) -> BriefBuilder:
    """A builder over the service's own reader, optionally under tightened limits."""
    return BriefBuilder(
        facts=service.facts,
        limits=service.user.observer.limits.model_copy(update=limits),
    )


async def _stale_row(
    service: RefinementService,
    *,
    src: str,
    dst: str,
    status: RefinementStatus,
    **kw: Any,
) -> int:
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


def test_the_rendered_brief_matches_the_golden_file():
    """The template is a pin (spec 21): regenerating this file is a deliberate edit.

    Regenerate with: ``PYTHONPATH=tests uv run python -c "from graph.test_brief import GOLDEN,
    GOLDEN_BRIEF; GOLDEN.write_text(GOLDEN_BRIEF.render(), encoding='utf-8')"``
    """
    assert GOLDEN_BRIEF.render() == GOLDEN.read_text(encoding="utf-8")


def _budget_brief() -> Brief:
    """The golden brief with every id stretched past the budget, which is what a real repo has."""
    long_note = GOLDEN_BRIEF.stale[0].model_copy(
        update={"target": f"{LONG_ID} -> {LONG_ID}"}
    )
    return GOLDEN_BRIEF.model_copy(
        update={
            "scope": LONG_ID,
            "targets": tuple(
                t.model_copy(
                    update={"node_id": f"{LONG_ID}{i}", "definers": (LONG_ID,)}
                )
                for i, t in enumerate(GOLDEN_BRIEF.targets)
            ),
            "stale": (long_note,),
            "staged": (STAGED.model_copy(update={"detail": f"{LONG_ID} is not here"}),),
        }
    )


@pytest.mark.parametrize(
    "brief", [GOLDEN_BRIEF, _budget_brief()], ids=["golden", "long"]
)
def test_the_rendered_brief_stays_inside_its_own_line_budget(brief: Brief):
    """Every producer folds, including the numbered header, a stale note and a staged verdict."""
    rendered = brief.render()
    assert EM_DASH not in rendered
    assert max(len(line) for line in rendered.splitlines()) <= LINE_WIDTH


async def test_a_built_brief_stays_inside_the_budget_too(refine_service):
    rendered = (await _builder(refine_service).build("")).render()
    assert max(len(line) for line in rendered.splitlines()) <= LINE_WIDTH


def test_an_empty_targets_section_keeps_its_blank_line():
    """`Stale corrections` must not run straight on from the "nothing here" line."""
    rendered = GOLDEN_BRIEF.model_copy(update={"targets": ()}).render()
    assert "unresolved.\n\nStale corrections" in rendered


def test_a_definer_list_is_capped_the_way_the_queue_payload_caps_it():
    """A node can have dozens of definers, and each one is prompt the run pays for."""
    row = UnresolvedRow(
        node_id="a.py::f",
        name="g",
        reason=UnresolvedReason.AMBIGUOUS_NAME,
        fact_kind=FactKind.CALLEE,
        definers=tuple(f"d{i}.py::g" for i in range(QUEUE_ID_CAP + 5)),
        candidates=tuple(f"c{i}.py::g" for i in range(QUEUE_ID_CAP + 5)),
    )
    target = BriefTarget.of(row, path="a.py", line=1, facts=())
    assert len(target.definers) == QUEUE_ID_CAP
    assert len(target.candidates) == QUEUE_ID_CAP


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


#: the same-module bare call `Impl.run` -> `_local` the resolver places, so an eval can mask it
MASKED_LOCAL = UnresolvedRow(
    node_id=QUEUED_CALL,
    fact_kind=FactKind.CALLEE,
    name="_local",
    reason=UnresolvedReason.UNIMPORTABLE_NAME,
    call_form=CallForm.BARE,
    definers=("impl.py::_local",),
)
MASKED_EDGE = Proposal(
    kind=RefinementKind.ADD_EDGE,
    target=RefinementTarget(
        src=QUEUED_CALL,
        dst="impl.py::_local",
        edge_kind=EdgeKind.CALLS,
        name="_local",
    ),
    reason="Impl.run calls _local, which impl.py defines just below it",
)


def _synthetic(service: RefinementService, *rows: UnresolvedRow) -> RefinementService:
    """A second service over the same index whose reader answers the queue from ``rows`` alone."""
    return RefinementService(
        service.index,
        service.root,
        service.settings,
        service.user,
        registry=RunRegistry(),
        facts=service.facts.model_copy(update={"synthetic": rows}),
    )


async def test_a_synthetic_row_is_the_whole_queue_the_brief_sees(refine_service):
    """A masked row must look unresolved, and this repo's own rows must not join it."""
    service = _synthetic(refine_service, MASKED_LOCAL)
    brief = await _builder(service).build("")
    assert [t.node_id for t in brief.targets] == [QUEUED_CALL]
    assert [t.name for t in brief.targets] == ["_local"]
    assert brief.queue_total == 1


async def test_a_synthetic_row_is_what_queue_row_answers_with(refine_service):
    row = await _synthetic(refine_service, MASKED_LOCAL).facts.queue_row(MASKED_EDGE)
    assert row == MASKED_LOCAL


async def test_three_synthetic_rows_brief_exactly_three_targets(refine_service):
    """The cap is twelve, so a short batch filled from the index would spend turns on rows no
    trial can score."""
    rows = tuple(
        MASKED_LOCAL.model_copy(update={"name": f"_local{i}"}) for i in range(3)
    )
    brief = await _builder(_synthetic(refine_service, *rows)).build("")
    assert len(brief.targets) == 3
    assert brief.queue_total == 3


async def test_an_externally_bound_synthetic_row_still_reaches_the_brief(
    refine_service,
):
    """The collision suite is made of the rows a brief hides by default, so a reader holding them
    must show them: hiding them briefed nothing and the control measured nothing."""
    bound = MASKED_LOCAL.model_copy(update={"externally_bound": True})
    brief = await _builder(_synthetic(refine_service, bound)).build("")
    assert [t.node_id for t in brief.targets] == [QUEUED_CALL]
    assert brief.queue_total == 1


async def test_a_scope_still_narrows_the_synthetic_rows(refine_service):
    service = _synthetic(refine_service, MASKED_LOCAL)
    assert (await _builder(service).build("svc.py")).targets == ()
    assert len((await _builder(service).build("impl.py")).targets) == 1


async def test_a_masked_row_makes_its_proposal_tier_b(refine_service):
    """Invariant 5: the eval measures the verifier-bounded path, not a tier C fallback."""
    service = _synthetic(refine_service, MASKED_LOCAL)
    run = await service.begin()
    verdict = await service.propose(run.run_id, MASKED_EDGE)
    assert verdict.tier is Tier.B
    assert verdict.verify is VerifyStatus.OK


async def test_without_the_masked_row_the_same_proposal_is_tier_c(refine_service):
    run = await refine_service.begin()
    verdict = await refine_service.propose(run.run_id, MASKED_EDGE)
    assert verdict.tier is Tier.C
