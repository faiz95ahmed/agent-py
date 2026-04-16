# agent-py

A CLI that gives an LLM agent interactive Python debugging — set breakpoints, run a program, and inspect state when it pauses — one tool call at a time.

## Why

Agents work in discrete, turn-based tool calls. A debugger is interactive: it runs, pauses, you inspect, you continue. `agent-py` bridges the two by keeping a debug session alive in a background daemon that holds the `debugpy`/DAP socket, so each CLI invocation is just "tell the running session what to do next and return the result."

## Install

```sh
uv sync
```

Runs on Python ≥ 3.10. macOS and Linux (uses Unix-domain sockets).

## Typical session

```sh
# 1. Decide where to pause. Breakpoints persist in ./.agent-py/breakpoints.json.
agent-py break path/to/script.py:42
agent-py break path/to/script.py:57 --condition "x > 100"

# 2. Launch the debuggee in the background under debugpy.
agent-py launch path/to/script.py -- --some arg

# 3. Attach. Spawns a daemon that holds the DAP connection and blocks until
#    the program hits a breakpoint, an exception, or exits.
agent-py connect
# → { "ok": true, "status": "paused", "pause": { "file": ..., "line": 42, "stack": [...], "source": [...] } }

# 4. Inspect the paused state.
agent-py listvars                  # variables in the innermost frame
agent-py variable 17               # expand a composite by its ref id
agent-py eval "len(items)"         # evaluate an expression in the current frame
agent-py frame 1                   # switch to a different stack frame

# 5. Control execution. Each of these blocks until the next pause/exit.
agent-py step over
agent-py step into
agent-py step out
agent-py continue

# 6. Clean up.
agent-py kill
```

Every command prints a single JSON object to stdout — easy to parse from a tool call.

## Commands

| Command | What it does |
|---|---|
| `break FILE:LINE [--condition EXPR]` | Add a breakpoint. Updates live if a session is running. |
| `unbreak FILE:LINE` | Remove a breakpoint. |
| `breakpoints` | List stored breakpoints. |
| `launch SCRIPT [-- ARGS...]` | Spawn the debuggee under `debugpy --listen --wait-for-client`. |
| `connect [--break-on uncaught\|raised\|none]` | Start the daemon, attach, run until the first pause/exit. |
| `continue` | Resume until the next pause/exit. |
| `step {over,into,out}` | DAP `next` / `stepIn` / `stepOut`. |
| `eval EXPR` | Evaluate an expression in the current frame. |
| `listvars [--page N]` | List variables in the current frame (paginated, 10/page). |
| `frame INDEX` | Switch the active stack frame. |
| `variable REF [--page N]` | Expand a composite by the ref id returned in `listvars`. |
| `status` | Show daemon + debuggee state. |
| `kill` | Terminate daemon + debuggee, clear session. |

### Output shape

- **Variables.** Scalars come back as `{"name", "type", "value"}`. Composites come back as `{"name", "type", "ref", "preview"}` — expand the `ref` in a follow-up `variable` call. This keeps large objects out of the tool result; the agent chooses what to drill into.
- **Pauses.** Every paused-state reply includes the stopped reason, the current file + line with ±3 lines of source context, and the full stack trace.
- **Dunders** (`__foo__`) are filtered from `listvars`/`variable` output. Long `repr`s are truncated to 50 characters.

## State on disk

Per working directory:

```
./.agent-py/
  breakpoints.json    # persistent breakpoints
  session.json        # daemon pid, dap port, current status, last pause
  daemon.log          # daemon stderr (useful if something is wrong)
```

The daemon's IPC socket lives under `$TMPDIR/agent-py-<hash>.sock` (macOS caps `AF_UNIX` paths at ~104 bytes, so it can't live inside the project dir).

One session per working directory. Run `agent-py kill` before starting another.

## Tests

```sh
uv run pytest
```

The end-to-end test launches a real debuggee, attaches, hits a breakpoint, lists variables, paginates a list, switches frames, and continues to termination.
