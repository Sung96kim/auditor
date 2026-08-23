# manifest reference

`manifest` prints the AST class and function manifest for one Python file. No detectors run, no
config is loaded, and no index is touched. `auditr manifest --help` lists every flag. The argument
must be an existing `.py` or `.pyi` file that parses.

## Common invocations

```bash
# table of every class, function, and method
auditr manifest path/to/file.py

# raw JSON for a script or an agent
auditr manifest path/to/file.py --json
```

- A non-Python file exits with an error saying so, and so does a file that fails to parse
  (the syntax error is included).

## What the manifest contains

- One entry per top-level class, per top-level function, and per method defined directly on a
  top-level class, in document order.
- Functions and classes nested inside a function, and classes nested inside a class, are not
  listed.
- Fields per entry: `line`, `symbol`, `kind` (`class`, `function`, or `method`), `arg_count`,
  `return_type`, `field_count`, `decorators`, `is_async`, and `flags`.
- The table shows `line`, `kind`, and `symbol`; the JSON carries every field.

## When to use it

- To get a file's structure without paying for a full audit, for example before deciding what to
  extract or where a class has grown too large.
- To audit the same file, use [report.md](report.md) or [scan.md](scan.md); those run detectors
  and do not emit the manifest.
- The same manifest is available over MCP as the `manifest` tool. See
  [auditr-mcp.md](auditr-mcp.md).
