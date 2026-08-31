# Agent Runtime Hooks Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the first, behavior-neutral phase of the typed in-process Hook Manager defined in the accepted Hook system design.

**Architecture:** Add immutable Hook event types and one asynchronous manager under `src/runtime`. The manager runs registrations deterministically, isolates observer failures, fails closed for Guard failures, supports bounded execution, and exposes errors without connecting to the orchestrator yet.

**Tech Stack:** Python 3.13, dataclasses, asyncio, enum, standard-library unittest.

---

### Task 1: Define Hook behavior with failing tests

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/runtime/__init__.py`
- Create: `tests/runtime/test_hooks.py`

**Step 1: Write tests for registration and ordering**

Create async tests proving that registrations are filtered by event and executed by ascending priority with registration order as the tie breaker.

**Step 2: Write tests for Observer failure isolation**

Verify that an Observer exception or timeout is returned as a failure record while later Observers still run.

**Step 3: Write tests for Guard behavior**

Verify explicit denial short-circuits later Guards and that a Guard exception or timeout returns a fail-closed decision.

**Step 4: Write tests for replacement and registration deduplication**

Verify a successful Guard may return replacement data and duplicate registration names are rejected.

**Step 5: Run tests to verify they fail**

Run:

```bash
./.venv/bin/python -m unittest tests.runtime.test_hooks -v
```

Expected: import failure because `src.runtime.hooks` does not exist.

### Task 2: Implement the Hook Manager

**Files:**
- Create: `src/runtime/hooks.py`

**Step 1: Define immutable public types**

Add:

```python
class HookEvent(StrEnum): ...
class HookKind(StrEnum): ...
@dataclass(frozen=True) class HookContext: ...
@dataclass(frozen=True) class HookDecision: ...
@dataclass(frozen=True) class HookFailure: ...
@dataclass(frozen=True) class HookRunResult: ...
@dataclass(frozen=True) class HookRegistration: ...
```

`HookContext.data` uses an immutable mapping view. `HookDecision` carries `allowed`, `reason`, and optional `replacement`.

**Step 2: Implement registration**

`HookManager.register()` rejects duplicate names and stores a monotonically increasing registration index. `register_many()` is a convenience wrapper.

**Step 3: Implement bounded Observer execution**

Use `asyncio.wait_for()` per Hook. Observer exceptions and timeouts append `HookFailure`, then execution continues.

**Step 4: Implement fail-closed Guard execution**

Guard exceptions, invalid return types, and timeouts produce `HookDecision(allowed=False, ...)`. Explicit denial stops later Guards. A successful replacement is carried in `HookRunResult`.

**Step 5: Protect error reporting from recursion**

The first phase returns structured failures to the caller rather than recursively emitting `ON_ERROR`. Runtime integration will decide how to publish those failures in Phase 2.

**Step 6: Run focused tests**

Run:

```bash
./.venv/bin/python -m unittest tests.runtime.test_hooks -v
```

Expected: all Hook tests pass.

### Task 3: Verify behavior and repository health

**Files:**
- Verify: `src/runtime/hooks.py`
- Verify: `tests/runtime/test_hooks.py`

**Step 1: Compile changed Python files**

```bash
./.venv/bin/python -m compileall src/runtime/hooks.py tests/runtime/test_hooks.py
```

Expected: successful compilation.

**Step 2: Run all standard-library tests**

```bash
./.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass.

**Step 3: Check formatting hazards**

```bash
git diff --check
```

Expected: no output.

**Step 4: Confirm behavior-neutral scope**

Verify no production caller imports `src.runtime.hooks` yet. Phase 1 must not alter Discord, Web, Pi, scheduler, routing, Tool Calling, or response behavior.

**Step 5: Commit**

```bash
git add docs/plans/2026-08-31-agent-runtime-hooks-implementation.md src/runtime/hooks.py tests
git commit -m "feat: add agent runtime hook manager"
```
