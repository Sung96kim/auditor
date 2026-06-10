#!/usr/bin/env bash
#
# release.sh — build and publish the auditor package to PyPI (or TestPyPI).
#
# Design / decisions:
#   * uv-native: `uv build` (hatchling backend) + `uv publish`. No twine/build needed,
#     though `uvx twine check` validates the artifacts' metadata before upload.
#   * Auth via API token in $UV_PUBLISH_TOKEN (a `pypi-…` token). For TestPyPI, set the
#     same var to a test.pypi.org token when using --test. Trusted publishing (OIDC) is
#     CI-only and not used by this local script.
#   * The version is the single source of truth in pyproject.toml; this script HARD-FAILS
#     if auditor/__init__.py's __version__ has drifted out of sync (they're duplicated).
#   * Safe by default: clean-tree + tests + lint + ruff-format + "not already on PyPI"
#     gates run before anything is built, and an interactive confirm precedes upload.
#
# Usage:
#   UV_PUBLISH_TOKEN=pypi-xxxx scripts/release.sh            # release current version to PyPI
#   UV_PUBLISH_TOKEN=pypi-xxxx scripts/release.sh --test     # dry-run to TestPyPI
#   scripts/release.sh --set-version 0.2.0                   # bump both version files, commit, exit
#   scripts/release.sh --yes --no-tag                        # non-interactive, skip git tag+push
#
# Options:
#   --test            publish to TestPyPI (https://test.pypi.org) instead of PyPI
#   --set-version X   rewrite the version in pyproject.toml + auditor/__init__.py, commit, exit
#   --no-tag          do not create/push a git tag after a successful publish
#   --skip-checks     skip the test/lint/format gates (NOT recommended)
#   --yes, -y         skip the confirmation prompt
#   --help, -h        show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# --- palette + log helpers -----------------------------------------------------------------
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
  YEL=$'\033[33m'; CYA=$'\033[36m'; MAG=$'\033[35m'; RST=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GRN=""; YEL=""; CYA=""; MAG=""; RST=""
fi

banner() { # title — printed once at the top
  printf '\n  %s%s◆ %s%s\n' "$BOLD" "$MAG" "$1" "$RST"
  printf '  %s%s%s\n\n' "$DIM" "$2" "$RST"
}
step() { printf '\n%s%s▸%s %s%s%s\n' "$BOLD" "$CYA" "$RST" "$BOLD" "$*" "$RST"; }
ok()   { printf '    %s✓%s %s\n' "$GRN" "$RST" "$*"; }
info() { printf '    %s·%s %s%s%s\n' "$DIM" "$RST" "$DIM" "$*" "$RST"; }
warn() { printf '    %s⚠%s  %s\n' "$YEL" "$RST" "$*" >&2; }
field(){ printf '      %s%-10s%s %s\n' "$DIM" "$1" "$RST" "$2"; }
die()  { printf '\n  %s%s✗ error%s %s\n\n' "$BOLD" "$RED" "$RST" "$*" >&2; exit 1; }

# --- args ----------------------------------------------------------------------------------
TARGET="pypi"; DO_TAG=1; RUN_CHECKS=1; ASSUME_YES=0; SET_VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --test) TARGET="testpypi" ;;
    --no-tag) DO_TAG=0 ;;
    --skip-checks) RUN_CHECKS=0 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --set-version) SET_VERSION="${2:-}"; shift ;;
    --help|-h) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

PYPROJECT="$REPO_ROOT/pyproject.toml"
INIT="$REPO_ROOT/auditor/__init__.py"

# extract a quoted value, tolerant of trailing inline comments (`[^"]*`, not greedy `.*`)
read_pyproject_version() { grep -m1 '^version = ' "$PYPROJECT" | sed -E 's/^version = "([^"]*)".*/\1/'; }
read_init_version()      { grep -m1 '^__version__ = ' "$INIT" | sed -E 's/^__version__ = "([^"]*)".*/\1/'; }
read_pkg_name()          { grep -m1 '^name = ' "$PYPROJECT" | sed -E 's/^name = "([^"]*)".*/\1/'; }

# --- --set-version: rewrite both files atomically, commit, exit -----------------------------
if [[ -n "$SET_VERSION" ]]; then
  [[ "$SET_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([abrc.0-9]+)?$ ]] || die "invalid version: $SET_VERSION"
  banner "set version" "$(read_pyproject_version) → $SET_VERSION"
  step "Rewriting version in pyproject.toml + auditor/__init__.py"
  sed -i -E "0,/^version = \".*\"/s//version = \"$SET_VERSION\"/" "$PYPROJECT"
  sed -i -E "s/^__version__ = \".*\"/__version__ = \"$SET_VERSION\"/" "$INIT"
  ok "pyproject.toml  → $(read_pyproject_version)"
  ok "auditor/__init__.py  → $(read_init_version)"
  git add "$PYPROJECT" "$INIT"
  git commit -q -m "chore: bump version to $SET_VERSION"
  ok "committed — review, then run ${BOLD}scripts/release.sh${RST} to publish"
  echo
  exit 0
fi

# --- gather + sync-check version -----------------------------------------------------------
VERSION="$(read_pyproject_version)"
INIT_VERSION="$(read_init_version)"
PKG_NAME="$(read_pkg_name)"
[[ -n "$VERSION" ]] || die "could not read version from pyproject.toml"
[[ "$VERSION" == "$INIT_VERSION" ]] || die "version drift: pyproject.toml=$VERSION but __init__.py=$INIT_VERSION (use --set-version to sync)"

banner "auditor release" "$PKG_NAME $VERSION  →  $TARGET"
ok "version files in sync ($VERSION)"

# --- preflight gates -----------------------------------------------------------------------
step "Preflight"

[[ -z "$(git status --porcelain)" ]] || die "working tree is dirty — commit or stash first"
ok "git working tree clean"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$BRANCH" != "main" ]]; then warn "on branch '$BRANCH', not 'main'"; else ok "on main"; fi

if [[ -n "${UV_PUBLISH_TOKEN:-}" ]]; then ok "UV_PUBLISH_TOKEN set"
else warn "UV_PUBLISH_TOKEN not set — uv publish will prompt for credentials"; fi

INDEX_HOST="pypi.org"; [[ "$TARGET" == "testpypi" ]] && INDEX_HOST="test.pypi.org"
ver_code="$(curl -s -o /dev/null -w '%{http_code}' "https://${INDEX_HOST}/pypi/${PKG_NAME}/${VERSION}/json" || echo 000)"
proj_code="$(curl -s -o /dev/null -w '%{http_code}' "https://${INDEX_HOST}/pypi/${PKG_NAME}/json" || echo 000)"
if [[ "$ver_code" == "200" ]]; then
  die "${PKG_NAME} ${VERSION} is already on ${INDEX_HOST} — bump the version (--set-version) first"
fi
if [[ "$proj_code" == "200" ]]; then
  warn "the name '${PKG_NAME}' already exists on ${INDEX_HOST} — publishing will 403 unless you own it"
  warn "if it's not yours, rename [project].name in pyproject.toml to an available one"
else
  ok "${PKG_NAME} ${VERSION} not yet on ${INDEX_HOST}"
fi

if [[ "$RUN_CHECKS" == "1" ]]; then
  step "Quality gates"
  info "ruff check…";   uv run ruff check auditor/ tests/ >/dev/null 2>&1 || die "ruff check failed"; ok "ruff check"
  uv run ruff format --check auditor/ tests/ >/dev/null 2>&1 && ok "ruff format" || warn "ruff format --check reported diffs (not blocking)"
  info "pytest…";       uv run pytest -q >/dev/null 2>&1 || die "tests failed"; ok "test suite passed"
else
  step "Quality gates"
  warn "skipped (--skip-checks)"
fi

# --- confirm -------------------------------------------------------------------------------
TAG="v${VERSION}"
step "Ready to release"
field "package" "$PKG_NAME $VERSION"
field "target"  "$TARGET ($INDEX_HOST)"
field "build"   "uv build → sdist + wheel"
if [[ "$DO_TAG" == "1" ]]; then field "git tag" "$TAG (push to origin)"; else field "git tag" "${DIM}skipped (--no-tag)${RST}"; fi
echo
if [[ "$ASSUME_YES" != "1" ]]; then
  printf '    %sproceed?%s [y/N] ' "$BOLD" "$RST"
  read -r reply </dev/tty || true
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted"
fi

# --- build ---------------------------------------------------------------------------------
step "Build"
rm -rf "$REPO_ROOT/dist"
uv build >/dev/null 2>&1 || die "uv build failed"
for f in dist/*; do info "$(basename "$f")"; done
ok "built sdist + wheel"

step "Validate metadata"
uvx twine check dist/* >/dev/null 2>&1 || die "twine check failed"
ok "twine check passed"

# --- publish -------------------------------------------------------------------------------
step "Publish → $TARGET"
if [[ "$TARGET" == "testpypi" ]]; then
  uv publish --publish-url https://test.pypi.org/legacy/ || die "uv publish (TestPyPI) failed"
else
  uv publish || die "uv publish failed"
fi
ok "published ${BOLD}${PKG_NAME} ${VERSION}${RST} to ${TARGET}"

# --- tag + push ----------------------------------------------------------------------------
if [[ "$DO_TAG" == "1" ]]; then
  step "Tag $TAG"
  if git rev-parse "$TAG" >/dev/null 2>&1; then
    warn "tag $TAG already exists — skipping"
  else
    git tag -a "$TAG" -m "release $TAG"
    git push origin "$TAG" >/dev/null 2>&1 || die "failed to push tag $TAG"
    ok "tagged and pushed $TAG"
  fi
fi

# --- done ----------------------------------------------------------------------------------
printf '\n  %s%s✓ released %s %s%s → %s%s\n' "$BOLD" "$GRN" "$PKG_NAME" "$VERSION" "$RST" "$TARGET" "$RST"
if [[ "$TARGET" == "testpypi" ]]; then
  printf '  %sinstall:%s uv pip install --index-url https://test.pypi.org/simple/ %s==%s\n\n' "$DIM" "$RST" "$PKG_NAME" "$VERSION"
else
  printf '  %sinstall:%s uv pip install %s==%s\n\n' "$DIM" "$RST" "$PKG_NAME" "$VERSION"
fi
