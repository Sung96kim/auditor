"""The real Readers against a real store; the transport tests use a fake (S8a P17's precedent)."""

import asyncio
from pathlib import Path

import auditor.observer.routes as routes_module
from auditor.graph.refine.models import Run
from auditor.observer.routes import Readers
from auditor.paths import repo_identity
from auditor.user_settings import UserSettings


def test_the_runs_reader_answers_an_empty_page_and_a_stable_tag(refine_repo: Path):
    """`/api/runs` reuses `LogQuery.page`, so an empty ledger is an empty page, not an error."""
    readers = Readers(settings=UserSettings())
    try:
        view = readers.runs(refine_repo)
        assert view.repo == str(refine_repo)
        assert view.log.runs == ()
        assert view.log.run_count == 0
        first = readers.runs_tag(refine_repo)
        assert first.endswith('-0-0.0"')  # an empty ledger: no runs, no newest start
        assert readers.runs_tag(refine_repo) == first
    finally:
        readers.close()


def test_one_handle_is_kept_per_identity_and_the_identity_is_resolved_once(
    refine_repo: Path, monkeypatch
):
    """`repo_identity` is an uncached git subprocess, so one request must not pay it three times."""
    identity = repo_identity(refine_repo)
    calls: list[Path] = []

    def counted(root: Path) -> str:
        calls.append(root)
        return identity

    monkeypatch.setattr(routes_module, "repo_identity", counted)
    readers = Readers(settings=UserSettings())
    try:
        assert readers.index(refine_repo, identity=identity) is readers.index(
            refine_repo, identity=identity
        )
        assert calls == []
        view = readers.runs(refine_repo, identity=identity)
        assert view.identity == identity
        assert calls == []
    finally:
        readers.close()


def test_the_run_detail_reader_answers_the_row_and_none_for_an_id_it_does_not_hold(
    refine_repo: Path,
):
    """`/api/runs/<id>` is the only reader with a 404 arm, so both arms are worth pinning (L-7)."""
    identity = repo_identity(refine_repo)
    readers = Readers(settings=UserSettings())
    try:
        index = readers.index(refine_repo, identity=identity)
        run = Run(repo_identity=identity, run_id="r-1", started_at=100.0)
        asyncio.run(index.runs.add_run(run))
        asyncio.run(
            index.runs.record_prompt("r-1", prompt="the brief", system_prompt_sha="sha")
        )
        view = readers.run(refine_repo, "r-1")
        assert view is not None
        assert view.run is not None
        assert view.run.run_id == "r-1"
        assert view.prompt == "the brief"
        assert view.refinements == ()
        assert view.trials == ()
        assert readers.run(refine_repo, "never-recorded") is None
    finally:
        readers.close()


def test_the_runs_tag_moves_when_a_run_lands(refine_repo: Path):
    """`/api/runs` is polled every 3 s: a tag that stopped tracking the rows would 304 forever."""
    readers = Readers(settings=UserSettings())
    try:
        identity = repo_identity(refine_repo)
        empty = readers.runs_tag(refine_repo)
        index = readers.index(refine_repo, identity=identity)
        asyncio.run(
            index.runs.add_run(
                Run(repo_identity=identity, run_id="r-1", started_at=100.0)
            )
        )
        assert readers.runs_tag(refine_repo) != empty
    finally:
        readers.close()


def test_a_settings_overlay_that_will_not_load_is_retried_rather_than_cached(
    refine_repo: Path, monkeypatch
):
    """Caching the fallback made one torn write permanent for the daemon's whole lifetime."""
    healthy = UserSettings()
    failures = {"left": 1}

    def torn(root: Path, **kw: object) -> UserSettings:
        if failures["left"]:
            failures["left"] -= 1
            raise OSError("the overlay was half written")
        return healthy

    monkeypatch.setattr(routes_module, "load_user_settings", torn)
    readers = Readers(settings=UserSettings())
    try:
        assert readers.user(refine_repo) is readers.settings
        assert (
            readers.user(refine_repo) is healthy
        )  # the failure was never written down
    finally:
        readers.close()


def test_two_repos_with_the_same_empty_ledger_do_not_share_a_tag(
    refine_repo: Path, git_repo: Path
):
    """The switcher polls one route for both, so a shared tag 304s repo B onto repo A's rows."""
    readers = Readers(settings=UserSettings())
    try:
        assert readers.runs_tag(refine_repo) != readers.runs_tag(git_repo)
    finally:
        readers.close()
