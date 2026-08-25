"""The refinement layer: the frozen records a correction is made of, in `models.py`, the id
namespace those records live in, in `namespace.py`, the pure merge a build applies them with,
in `overlay.py`, and the cross-process rebuild lock, in `lock.py`. Stdlib, pydantic and the
repo's own config only; no database."""
