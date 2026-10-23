# local

Laptop-only helpers for operating Software Factory workstreams. They import
factory-mono's CLI code, so this repository must sit beside a bootstrapped
checkout at `~/factory/factory-mono`: `tsconfig.json` and the `node_modules`
link both point there.

```bash
bun local/sf-content.ts pull <workstreamId> <dir>   # download skills, scripts, memory
bun local/sf-content.ts push <workstreamId> <dir>   # publish, 3-way merged against the pulled base
bun local/session-fetch.ts <sessionId> <out.json>   # every message of a session
bun local/memcheck.ts <memory.md>...                # memory parses, round-trips, notes in order
```

Scratch state for in-flight work lives in `work/`, which git ignores.
