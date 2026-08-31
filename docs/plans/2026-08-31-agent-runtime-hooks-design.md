# Agent Runtime Hook System Design

## Status

Accepted for implementation on 2026-08-31.

## 1. Background

MyMinions already has several callback-like extension points:

- `Capability.startup_handlers` starts schedulers and restores pending state.
- `run_tool_loop(..., on_tool=...)` publishes tool progress.
- The tool loop blocks writes after untrusted content has entered the context.
- `output_guard` sanitizes the final answer.
- tracing and evaluation code records selected lifecycle events.

These mechanisms work, but they are implemented in different layers and use different
interfaces. Adding another cross-cutting feature currently requires editing the
orchestrator, tool loop, or each entry point separately. The Hook system will unify
these extension points without replacing Capability, LangGraph, Tool Calling, or the
scheduler.

## 2. Goals

### Functional requirements

- Publish stable lifecycle events for messages, routes, tools, responses, errors, and
  application startup.
- Allow observer Hooks to record progress, traces, evaluation data, and UI flow state.
- Allow Guard Hooks to reject sensitive actions through an explicit decision object.
- Let a Capability contribute Hooks without making the runtime import that Capability.
- Preserve Discord, Web, Pi, scheduler, and existing command behavior during migration.
- Correlate all events from one request with one `trace_id`.

### Non-functional requirements

- A normal observer failure must not fail the user's request.
- A security Guard failure must reject the protected action.
- Hook execution must be deterministic and bounded by a timeout.
- Hook payloads must not contain API keys, full prompts, raw FIT contents, or private
  coordinates by default.
- The first implementation must remain in-process and require no message queue,
  database, or external service.
- Existing behavior must be migratable one extension point at a time.

## 3. Non-goals

- Hooks are not a replacement for LangGraph nodes or Agent reasoning.
- Hooks do not call the LLM in the first version.
- Hooks do not create a public third-party plugin marketplace.
- Hooks do not provide cross-process delivery guarantees.
- Hooks do not allow arbitrary mutation of the runtime context.

## 4. Approaches considered

### Continue adding callback parameters

This has the smallest immediate change, but every new concern adds another argument
such as `on_tool`, `on_error`, or `on_response`. Ordering, timeout, and failure policy
would remain inconsistent.

### Typed in-process Hook Manager

This adds one shared lifecycle model, explicit registration, deterministic ordering,
and bounded execution. It matches the current single-process Python architecture and
is the selected approach.

### External event bus

A queue such as Redis Streams or Kafka would decouple consumers and support replay,
but introduces deployment, persistence, and monitoring work that the current personal
Agent does not need.

## 5. High-level architecture

```text
Discord / Web / Pi
        |
        v
  MESSAGE_RECEIVED ---------------------------> observers
        |
        v
    BEFORE_ROUTE ---- guards ---- deny? ------> response
        |
      router
        |
    AFTER_ROUTE ------------------------------> trace / UI / eval
        |
    BEFORE_TOOL ----- guards ---- deny? ------> tool error result
        |
      tool handler
        |
    AFTER_TOOL -------------------------------> trace / UI / metrics
        |
  BEFORE_RESPONSE --- guards / transformers --> sanitized answer
        |
      send answer
        |
   AFTER_RESPONSE ----------------------------> history / eval / metrics

Any stage failure ----------------------------> ON_ERROR
Application boot -----------------------------> APP_STARTED
```

The Hook Manager belongs to the runtime. Capabilities may register Hook definitions,
but do not own execution order or failure policy. LangGraph nodes and scheduler jobs
may emit the same events when they enter the shared runtime.

## 6. Event model

The first version supports these events:

| Event | Purpose | May block or replace data |
| --- | --- | --- |
| `APP_STARTED` | Start schedulers and restore state | No |
| `MESSAGE_RECEIVED` | Trace request arrival | No |
| `BEFORE_ROUTE` | Enforce channel, source, and rate policy | Yes |
| `AFTER_ROUTE` | Record the selected Capability and reason | No |
| `BEFORE_TOOL` | Enforce read/write and trust boundaries | Yes |
| `AFTER_TOOL` | Record result metadata and duration | No |
| `BEFORE_RESPONSE` | Sanitize or replace final output | Yes |
| `AFTER_RESPONSE` | Record delivery and evaluation facts | No |
| `ON_ERROR` | Publish structured failures | No |

Events use a typed, immutable context:

```python
@dataclass(frozen=True)
class HookContext:
    event: HookEvent
    trace_id: str
    conversation_id: str
    source: str
    read_only: bool
    command: str | None = None
    tool: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
```

Hook code does not mutate `HookContext`. A Guard or transformer returns an explicit
decision:

```python
@dataclass(frozen=True)
class HookDecision:
    allowed: bool = True
    reason: str = ""
    replacement: Any = None
```

This makes cancellation and output replacement visible to tests and traces.

## 7. Hook types and execution policy

### Observer

Observers consume an event and return no decision. Examples include tracing, Web flow
visualization, evaluation capture, and Discord progress updates.

- Failure policy: fail open and emit `ON_ERROR`.
- Default timeout: 2 seconds.
- Execution: sequential in priority order for deterministic traces.
- Observers cannot override Guard decisions.

### Guard

Guards protect an operation and return `HookDecision`. Examples include channel
permissions, read-only mode, rate limiting, and blocking writes after untrusted input.

- Failure policy: security Guards fail closed.
- Default timeout: 2 seconds.
- Execution: sequential in priority order.
- First denial stops the remaining Guards for that event.
- Core security Guards run before Capability-provided Guards.

Priorities use lower numbers first:

- `0-19`: core security
- `20-49`: runtime policy
- `50-79`: Capability behavior
- `80-99`: tracing, UI, and evaluation observers

## 8. Components

The first version intentionally uses a small file set:

```text
src/runtime/hooks.py
src/runtime/builtin_hooks.py
tests/runtime/test_hooks.py
tests/integration/test_hook_lifecycle.py
```

`hooks.py` contains `HookEvent`, `HookContext`, `HookDecision`, `HookRegistration`, and
`HookManager`. `builtin_hooks.py` contains runtime-owned security, tracing, progress,
output, and error Hooks.

`Capability` gains an optional immutable `hooks` tuple. `CapabilityRegistry` collects
those registrations and supplies them to one process-wide Hook Manager. Capability
auto-discovery continues to work unchanged.

## 9. Data flow

1. An entry point creates a trace and emits `MESSAGE_RECEIVED`.
2. The orchestrator emits `BEFORE_ROUTE`, calls the router, then emits `AFTER_ROUTE`.
3. The tool loop emits `BEFORE_TOOL` before each handler and `AFTER_TOOL` after success,
   timeout, or handled failure.
4. The final answer passes through `BEFORE_RESPONSE`; an approved replacement becomes
   the new answer.
5. After delivery, the entry point emits `AFTER_RESPONSE`.
6. Unexpected exceptions emit `ON_ERROR` with a safe error category and trace ID.

Hook metadata contains summaries rather than raw content. Tool events include name,
duration, result size, trust state, write state, and success status. Response events
include character count and output type, not the complete private answer.

## 10. Migration plan

### Phase 1: foundation

- Add Hook types, manager, ordering, timeout, and error isolation.
- Create unit tests before connecting production paths.
- Keep all current callbacks active.

### Phase 2: compatibility adapters

- Adapt `startup_handlers` to `APP_STARTED` without changing Capability declarations.
- Adapt `on_tool` to a progress observer.
- Emit route, response, and error events from the orchestrator.

### Phase 3: policy migration

- Move write-after-untrusted enforcement to a core `BEFORE_TOOL` Guard.
- Move output sanitization to a core `BEFORE_RESPONSE` transformer.
- Keep regression assertions proving the old security behavior is unchanged.

### Phase 4: consumers

- Drive the Web architecture graph from emitted lifecycle events.
- Capture evaluation facts from `AFTER_ROUTE`, `AFTER_TOOL`, and `AFTER_RESPONSE`.
- Send user-safe operational failures through an `ON_ERROR` observer.

### Phase 5: distribution

- Sync the stable runtime implementation into `coros-running-agent`.
- Let `pi-coros-running-agent` display trace metadata returned by the backend; do not
  duplicate the Python Hook runtime inside the Pi adapter.

## 11. Failure modes

| Failure | Impact | Mitigation |
| --- | --- | --- |
| Observer raises an exception | Missing telemetry | Log, emit safe error event, continue |
| Observer exceeds timeout | Slow response | Cancel after 2 seconds and continue |
| Security Guard raises or times out | Policy cannot be verified | Fail closed with a user-safe reason |
| Hook recursively emits itself | Infinite loop | Mark internal error events and prohibit nested `ON_ERROR` emission |
| Hook payload contains secrets | Sensitive logs | Allowlisted metadata plus existing output redaction |
| Duplicate startup emission | Duplicate schedulers | Manager tracks one-time Hook registrations and startup execution |
| Hook changes execution order | Behavioral regression | Stable priority ordering and integration snapshots |

## 12. Testing strategy

### Unit tests

- Registration and deterministic priority order.
- Observer timeout and fail-open behavior.
- Guard denial, fail-closed behavior, and short-circuiting.
- Replacement output from `BEFORE_RESPONSE`.
- Duplicate registration and recursive error protection.

### Integration tests

- One natural-language request emits the expected event sequence.
- A tool timeout still emits `AFTER_TOOL` and `ON_ERROR` metadata.
- Read-only Web requests cannot execute write tools.
- External/RAG content followed by a write attempt remains blocked.
- Startup registers each scheduler exactly once.
- Discord and Web receive the same trace ID and lifecycle facts.

### Regression tests

- Existing command routing remains unchanged.
- COROS auto reports and sleep reports still start normally.
- Passthrough reports are not rewritten by response Hooks.
- Photo and FIT privacy rules remain enforced.

## 13. Operational considerations

- No new infrastructure or dependency is required.
- Hook timings are included in structured trace logs.
- Slow and failed Hook counts can be added to the existing observability summary.
- Hook names and event names are stable public runtime contracts; payload details may
  evolve through optional fields.
- Expensive work such as LLM evaluation or network notification must remain outside the
  synchronous Hook path or be scheduled separately.

## 14. ADR-001: Adopt a typed in-process Hook Manager

### Status

Accepted.

### Context

The Agent needs shared lifecycle extension points for security, progress, evaluation,
tracing, and future memory auditing. Existing callbacks do not share contracts or
failure behavior. The application currently runs as a Python monolith on one VPS.

### Decision

Adopt a typed, asynchronous, in-process Hook Manager with immutable contexts, explicit
Guard decisions, deterministic priority ordering, bounded execution, and Capability
registration. Observers fail open; security Guards fail closed.

### Consequences

Positive consequences are consistent observability, simpler feature integration, and
testable security boundaries. Negative consequences are additional lifecycle plumbing
and the need to keep Hook payloads stable. The system remains process-local and does
not provide durable delivery, which is acceptable for the current deployment.

### Alternatives considered

Callback parameters were rejected because they keep cross-cutting behavior scattered.
An external event bus was rejected because its operational cost is not justified by
the current scale.

## 15. Acceptance criteria

- Existing Discord and Web behavior passes regression tests.
- The runtime emits the documented event sequence with one trace ID.
- Security Guards cannot be bypassed by Capability Hook priority.
- No Hook can delay a request indefinitely.
- The Web flow graph can render from real Hook events instead of a hard-coded path.
- Open-source synchronization does not expose private prompts, keys, memory, FIT files,
  coordinates, or user data.
