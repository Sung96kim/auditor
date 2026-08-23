# discover reference

`discover` lists the files a scan of the same target would audit, each with its classified role.
No detectors run and no index is touched. `auditr discover --help` lists every flag. The target
must exist; nothing else is required.

## Common invocations

```bash
# table of file and role
auditr discover .

# raw JSON
auditr discover . --json

# check what a config change would do to the file set before scanning
auditr discover . --config-json '{"exclude":["**/vendor/**"]}'
```

- The listing is the same file set `auditr scan` would walk for that target, so it is the fastest
  way to verify an `exclude` or `role_globs` change. See [configuration.md](configuration.md).
- There is no `--include-gitignored` here; `discover` follows the configured `respect_gitignore`.

## What is listed

- Files whose extension belongs to a registered language, plus filename-keyed manifests.
  `auditr plugins list` prints the registered languages. See [plugins.md](plugins.md).
- Left out: git-ignored files, the built-in vendor and build directories (`.git`, `.venv`,
  `venv`, `node_modules`, `build`, `dist`, the tool caches, `.auditor`), the configured `exclude`
  globs, and generated patterns such as `*_pb2.py`, `*.gen.ts`, and `*.d.ts`.
- Soft-skipped on a directory listing: `migrations/` directories and Alembic version directories.
  Point `discover` straight at one to list its files.
- Inside a git repo the file list comes from `git ls-files`; outside one it is a tree walk.

## Roles

- One of `production`, `test`, `test_support`, `script`, or `generated`.
- `generated`: a `_pb2.py`, `_pb2_grpc.py`, or `.gen.py` name, or a generated-by marker near the
  top of the file.
- `test_support`: `conftest.py`, `factories.py`, `factory.py`, `fixtures.py`, anything under a
  `fixtures/` or `factories/` directory, and any module under `tests/` that defines no test.
- `test`: a `tests/` or `test/` path segment, a `test_*.py` or `*_test.py` name, or a module that
  imports pytest or unittest and defines tests.
- `script`: a module with an `if __name__ == "__main__":` block.
- `production`: everything else.
- `role_globs` in config overrides the classification per glob and is checked before every
  heuristic. See [configuration.md](configuration.md).
- Role decides rule strength during a scan; `auditr scan -t` audits test-role files at production
  strength. See [scan.md](scan.md).
