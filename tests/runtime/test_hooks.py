import asyncio
import unittest

from src.runtime.hooks import (
    HookContext,
    HookDecision,
    HookEvent,
    HookKind,
    HookManager,
    HookRegistration,
)


class HookManagerTests(unittest.IsolatedAsyncioTestCase):
    def context(self, event: HookEvent) -> HookContext:
        return HookContext(
            event=event,
            trace_id="trace-1",
            conversation_id="conversation-1",
            source="test",
            read_only=False,
        )

    async def test_runs_matching_hooks_by_priority_then_registration_order(self) -> None:
        calls: list[str] = []

        async def record(name: str) -> None:
            calls.append(name)

        manager = HookManager()
        manager.register_many(
            (
                HookRegistration(
                    name="late",
                    event=HookEvent.AFTER_ROUTE,
                    kind=HookKind.OBSERVER,
                    handler=lambda _: record("late"),
                    priority=80,
                ),
                HookRegistration(
                    name="early-a",
                    event=HookEvent.AFTER_ROUTE,
                    kind=HookKind.OBSERVER,
                    handler=lambda _: record("early-a"),
                    priority=20,
                ),
                HookRegistration(
                    name="early-b",
                    event=HookEvent.AFTER_ROUTE,
                    kind=HookKind.OBSERVER,
                    handler=lambda _: record("early-b"),
                    priority=20,
                ),
                HookRegistration(
                    name="other-event",
                    event=HookEvent.AFTER_TOOL,
                    kind=HookKind.OBSERVER,
                    handler=lambda _: record("other-event"),
                ),
            )
        )

        result = await manager.emit(self.context(HookEvent.AFTER_ROUTE))

        self.assertEqual(calls, ["early-a", "early-b", "late"])
        self.assertEqual(result.executed, ("early-a", "early-b", "late"))
        self.assertTrue(result.decision.allowed)
        self.assertEqual(result.failures, ())

    async def test_observer_exception_is_recorded_and_later_observer_runs(self) -> None:
        calls: list[str] = []

        async def fail(_: HookContext) -> None:
            raise ValueError("private failure detail")

        async def continue_running(_: HookContext) -> None:
            calls.append("continued")

        manager = HookManager()
        manager.register_many(
            (
                HookRegistration(
                    name="broken-observer",
                    event=HookEvent.AFTER_TOOL,
                    kind=HookKind.OBSERVER,
                    handler=fail,
                ),
                HookRegistration(
                    name="healthy-observer",
                    event=HookEvent.AFTER_TOOL,
                    kind=HookKind.OBSERVER,
                    handler=continue_running,
                ),
            )
        )

        result = await manager.emit(self.context(HookEvent.AFTER_TOOL))

        self.assertEqual(calls, ["continued"])
        self.assertTrue(result.decision.allowed)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].error_type, "ValueError")
        self.assertNotIn("private failure detail", result.failures[0].reason)

    async def test_observer_timeout_is_fail_open(self) -> None:
        calls: list[str] = []

        async def slow(_: HookContext) -> None:
            await asyncio.sleep(0.05)

        async def continue_running(_: HookContext) -> None:
            calls.append("continued")

        manager = HookManager()
        manager.register_many(
            (
                HookRegistration(
                    name="slow-observer",
                    event=HookEvent.MESSAGE_RECEIVED,
                    kind=HookKind.OBSERVER,
                    handler=slow,
                    timeout_seconds=0.005,
                ),
                HookRegistration(
                    name="healthy-observer",
                    event=HookEvent.MESSAGE_RECEIVED,
                    kind=HookKind.OBSERVER,
                    handler=continue_running,
                ),
            )
        )

        result = await manager.emit(self.context(HookEvent.MESSAGE_RECEIVED))

        self.assertEqual(calls, ["continued"])
        self.assertTrue(result.decision.allowed)
        self.assertTrue(result.failures[0].timed_out)

    async def test_guard_denial_skips_later_guards_but_runs_observers(self) -> None:
        calls: list[str] = []

        async def deny(_: HookContext) -> HookDecision:
            calls.append("deny")
            return HookDecision(allowed=False, reason="write blocked")

        async def skipped_guard(_: HookContext) -> HookDecision:
            calls.append("skipped")
            return HookDecision()

        async def observe(_: HookContext) -> None:
            calls.append("observe")

        manager = HookManager()
        manager.register_many(
            (
                HookRegistration(
                    name="security-guard",
                    event=HookEvent.BEFORE_TOOL,
                    kind=HookKind.GUARD,
                    handler=deny,
                    priority=0,
                ),
                HookRegistration(
                    name="later-guard",
                    event=HookEvent.BEFORE_TOOL,
                    kind=HookKind.GUARD,
                    handler=skipped_guard,
                    priority=20,
                ),
                HookRegistration(
                    name="audit-observer",
                    event=HookEvent.BEFORE_TOOL,
                    kind=HookKind.OBSERVER,
                    handler=observe,
                    priority=80,
                ),
            )
        )

        result = await manager.emit(self.context(HookEvent.BEFORE_TOOL))

        self.assertEqual(calls, ["deny", "observe"])
        self.assertFalse(result.decision.allowed)
        self.assertEqual(result.decision.reason, "write blocked")
        self.assertEqual(result.executed, ("security-guard", "audit-observer"))

    async def test_guard_exception_is_fail_closed(self) -> None:
        calls: list[str] = []

        async def fail(_: HookContext) -> HookDecision:
            raise RuntimeError("sensitive detail")

        async def observe(_: HookContext) -> None:
            calls.append("observe")

        manager = HookManager()
        manager.register_many(
            (
                HookRegistration(
                    name="broken-guard",
                    event=HookEvent.BEFORE_ROUTE,
                    kind=HookKind.GUARD,
                    handler=fail,
                ),
                HookRegistration(
                    name="audit-observer",
                    event=HookEvent.BEFORE_ROUTE,
                    kind=HookKind.OBSERVER,
                    handler=observe,
                ),
            )
        )

        result = await manager.emit(self.context(HookEvent.BEFORE_ROUTE))

        self.assertFalse(result.decision.allowed)
        self.assertIn("broken-guard", result.decision.reason)
        self.assertEqual(result.failures[0].error_type, "RuntimeError")
        self.assertNotIn("sensitive detail", result.failures[0].reason)
        self.assertEqual(calls, ["observe"])

    async def test_guard_timeout_is_fail_closed(self) -> None:
        async def slow(_: HookContext) -> HookDecision:
            await asyncio.sleep(0.05)
            return HookDecision()

        manager = HookManager()
        manager.register(
            HookRegistration(
                name="slow-guard",
                event=HookEvent.BEFORE_RESPONSE,
                kind=HookKind.GUARD,
                handler=slow,
                timeout_seconds=0.005,
            )
        )

        result = await manager.emit(self.context(HookEvent.BEFORE_RESPONSE))

        self.assertFalse(result.decision.allowed)
        self.assertTrue(result.failures[0].timed_out)

    async def test_guard_replacement_is_returned(self) -> None:
        replacement = {"answer": "sanitized"}

        async def replace(_: HookContext) -> HookDecision:
            return HookDecision(replacement=replacement)

        manager = HookManager()
        manager.register(
            HookRegistration(
                name="response-sanitizer",
                event=HookEvent.BEFORE_RESPONSE,
                kind=HookKind.GUARD,
                handler=replace,
            )
        )

        result = await manager.emit(self.context(HookEvent.BEFORE_RESPONSE))

        self.assertTrue(result.decision.allowed)
        self.assertIs(result.decision.replacement, replacement)

    async def test_invalid_guard_result_is_fail_closed(self) -> None:
        async def invalid(_: HookContext) -> None:
            return None

        manager = HookManager()
        manager.register(
            HookRegistration(
                name="invalid-guard",
                event=HookEvent.BEFORE_TOOL,
                kind=HookKind.GUARD,
                handler=invalid,
            )
        )

        result = await manager.emit(self.context(HookEvent.BEFORE_TOOL))

        self.assertFalse(result.decision.allowed)
        self.assertEqual(result.failures[0].error_type, "InvalidHookResult")

    async def test_duplicate_registration_name_is_rejected_atomically(self) -> None:
        async def observe(_: HookContext) -> None:
            return None

        manager = HookManager()
        manager.register(
            HookRegistration(
                name="duplicate",
                event=HookEvent.APP_STARTED,
                kind=HookKind.OBSERVER,
                handler=observe,
            )
        )

        with self.assertRaisesRegex(ValueError, "duplicate"):
            manager.register_many(
                (
                    HookRegistration(
                        name="new-hook",
                        event=HookEvent.APP_STARTED,
                        kind=HookKind.OBSERVER,
                        handler=observe,
                    ),
                    HookRegistration(
                        name="duplicate",
                        event=HookEvent.ON_ERROR,
                        kind=HookKind.OBSERVER,
                        handler=observe,
                    ),
                )
            )

        result = await manager.emit(self.context(HookEvent.APP_STARTED))
        self.assertEqual(result.executed, ("duplicate",))

    def test_context_data_is_shallowly_immutable(self) -> None:
        context = HookContext(
            event=HookEvent.MESSAGE_RECEIVED,
            trace_id="trace-1",
            conversation_id="conversation-1",
            source="test",
            read_only=True,
            data={"message_length": 12},
        )

        with self.assertRaises(TypeError):
            context.data["message_length"] = 13  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
