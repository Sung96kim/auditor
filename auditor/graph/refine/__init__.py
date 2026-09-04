"""The refinement layer: the frozen records a correction is made of, in `models.py`, the id
namespace those records live in, in `namespace.py`, the pure merge a build applies them with,
in `overlay.py`, the cross-process rebuild lock, in `lock.py`, and spec 11's knob tuning, in
`tuning.py` (policy, which `build.py` imports) and `trial.py` (the measurement, which imports it).
Those last two read and write `graph_tuning` through an `IndexStore`; the rest is stdlib, pydantic
and the repo's own config only."""
