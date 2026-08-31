import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


DEFAULT_HOOK_TIMEOUT_SECONDS = 2.0


class HookEvent(StrEnum):
    APP_STARTED = "app_started"
    MESSAGE_RECEIVED = "message_received"
    BEFORE_ROUTE = "before_route"
    AFTER_ROUTE = "after_route"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_RESPONSE = "before_response"
    AFTER_RESPONSE = "after_response"
    ON_ERROR = "on_error"


class HookKind(StrEnum):
    OBSERVER = "observer"
    GUARD = "guard"


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

    def __post_init__(self) -> None:
        if not isinstance(self.data, Mapping):
            raise TypeError("HookContext.data must be a mapping")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True)
class HookDecision:
    allowed: bool = True
    reason: str = ""
    replacement: Any = None


@dataclass(frozen=True)
class HookFailure:
    hook_name: str
    event: HookEvent
    error_type: str
    reason: str
    timed_out: bool = False


@dataclass(frozen=True)
class HookRunResult:
    decision: HookDecision = field(default_factory=HookDecision)
    failures: tuple[HookFailure, ...] = ()
    executed: tuple[str, ...] = ()


HookHandler = Callable[
    [HookContext], Awaitable[HookDecision | None]
]


@dataclass(frozen=True)
class HookRegistration:
    name: str
    event: HookEvent
    kind: HookKind
    handler: HookHandler
    priority: int = 50
    timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("hook name is required")
        if self.timeout_seconds <= 0:
            raise ValueError("hook timeout_seconds must be positive")


class HookManager:
    def __init__(self) -> None:
        self._registrations: list[tuple[int, HookRegistration]] = []
        self._names: set[str] = set()
        self._next_index = 0

    def register(self, registration: HookRegistration) -> None:
        self.register_many((registration,))

    def register_many(self, registrations: Sequence[HookRegistration]) -> None:
        pending = tuple(registrations)
        pending_names = [registration.name for registration in pending]
        duplicate_names = {
            name for name in pending_names if pending_names.count(name) > 1
        }
        duplicate_names.update(self._names.intersection(pending_names))
        if duplicate_names:
            names = ", ".join(sorted(duplicate_names))
            raise ValueError(f"duplicate hook registration: {names}")

        for registration in pending:
            self._registrations.append((self._next_index, registration))
            self._names.add(registration.name)
            self._next_index += 1

    async def emit(self, context: HookContext) -> HookRunResult:
        registrations = sorted(
            (
                (index, registration)
                for index, registration in self._registrations
                if registration.event == context.event
            ),
            key=lambda item: (item[1].priority, item[0]),
        )

        decision = HookDecision()
        failures: list[HookFailure] = []
        executed: list[str] = []
        guards_blocked = False

        for _, registration in registrations:
            if guards_blocked and registration.kind == HookKind.GUARD:
                continue

            executed.append(registration.name)
            result, failure = await self._run_one(registration, context)
            if failure is not None:
                failures.append(failure)
                if registration.kind == HookKind.GUARD:
                    decision = HookDecision(
                        allowed=False,
                        reason=f"hook {registration.name} could not verify the operation",
                    )
                    guards_blocked = True
                continue

            if registration.kind == HookKind.OBSERVER:
                if result is not None:
                    failures.append(
                        self._invalid_result_failure(registration, context.event)
                    )
                continue

            if not isinstance(result, HookDecision):
                failures.append(
                    self._invalid_result_failure(registration, context.event)
                )
                decision = HookDecision(
                    allowed=False,
                    reason=f"hook {registration.name} returned an invalid decision",
                )
                guards_blocked = True
                continue

            if not result.allowed:
                decision = result
                guards_blocked = True
                continue

            decision = HookDecision(
                allowed=True,
                reason=result.reason or decision.reason,
                replacement=(
                    result.replacement
                    if result.replacement is not None
                    else decision.replacement
                ),
            )

        return HookRunResult(
            decision=decision,
            failures=tuple(failures),
            executed=tuple(executed),
        )

    async def _run_one(
        self,
        registration: HookRegistration,
        context: HookContext,
    ) -> tuple[HookDecision | None, HookFailure | None]:
        try:
            pending = registration.handler(context)
            if not inspect.isawaitable(pending):
                return None, self._invalid_result_failure(registration, context.event)
            result = await asyncio.wait_for(
                pending,
                timeout=registration.timeout_seconds,
            )
            return result, None
        except TimeoutError:
            return None, HookFailure(
                hook_name=registration.name,
                event=context.event,
                error_type="TimeoutError",
                reason=(
                    f"hook {registration.name} timed out after "
                    f"{registration.timeout_seconds:g} seconds"
                ),
                timed_out=True,
            )
        except Exception as exc:
            error_type = type(exc).__name__
            return None, HookFailure(
                hook_name=registration.name,
                event=context.event,
                error_type=error_type,
                reason=f"hook {registration.name} raised {error_type}",
            )

    @staticmethod
    def _invalid_result_failure(
        registration: HookRegistration,
        event: HookEvent,
    ) -> HookFailure:
        return HookFailure(
            hook_name=registration.name,
            event=event,
            error_type="InvalidHookResult",
            reason=f"hook {registration.name} returned an invalid result",
        )
