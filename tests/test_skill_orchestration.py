"""Skill orchestration: skills route messages to one another and compose.

The executable SkillRegistry (kosika.skills_framework) gains a message bus:
`load_skill` registers a capability, `route` delivers a SkillMessage to its
recipient and returns a reply, and `tick_all` runs background processing.
A skill's implementation is a handler of (type, payload), so one skill can
route to another mid-handling — that is how capabilities compose.
"""

from __future__ import annotations

import unittest

from kosika.skills_framework import (
    Skill,
    SkillKind,
    SkillMessage,
    SkillOutput,
    SkillParameter,
    SkillRegistry,
    SkillStatus,
)


def echo_skill() -> Skill:
    return Skill(
        id="echo",
        name="Echo",
        kind=SkillKind.TOOL,
        description="echoes the message back",
        parameters=[
            SkillParameter("type", "Text"),
            SkillParameter("payload", "Record", required=False),
        ],
        output=SkillOutput("Record"),
        implementation=lambda type, payload=None: {"type": type, "payload": payload},
        status=SkillStatus.VALIDATED,
    )


def boom_skill() -> Skill:
    def handle(type, payload=None):
        raise RuntimeError("handler exploded")

    return Skill(
        id="boom",
        name="Boom",
        kind=SkillKind.TOOL,
        description="always fails",
        parameters=[SkillParameter("type", "Text")],
        output=SkillOutput("Record"),
        implementation=handle,
        status=SkillStatus.VALIDATED,
    )


class OrchestrationTests(unittest.TestCase):
    def test_load_skill_registers_a_queryable_capability(self) -> None:
        registry = SkillRegistry()
        self.assertEqual(registry.load_skill(echo_skill()), "echo")
        self.assertIsNotNone(registry.get("echo"))

    def test_route_delivers_a_message_and_returns_a_reply(self) -> None:
        registry = SkillRegistry()
        registry.load_skill(echo_skill())
        msg = SkillMessage(sender="host", recipient="echo", type="ping", payload={"n": 1})
        reply = registry.route(msg)
        self.assertEqual(reply.sender, "echo")
        self.assertEqual(reply.recipient, "host")
        self.assertEqual(reply.type, "result")
        self.assertEqual(reply.reply_to, msg.id)
        self.assertEqual(reply.payload["value"], {"type": "ping", "payload": {"n": 1}})

    def test_route_to_an_unknown_skill_returns_an_error_reply(self) -> None:
        registry = SkillRegistry()
        reply = registry.route(SkillMessage("host", "nobody", "ping"))
        self.assertEqual(reply.type, "error")
        self.assertIn("nobody", reply.payload["problem"])

    def test_a_handler_failure_comes_back_as_an_error_reply(self) -> None:
        registry = SkillRegistry()
        registry.load_skill(boom_skill())
        reply = registry.route(SkillMessage("host", "boom", "ping"))
        self.assertEqual(reply.type, "error")
        self.assertIn("handler exploded", reply.payload["problem"])

    def test_tick_all_runs_background_processing(self) -> None:
        registry = SkillRegistry()
        seen: list[float] = []
        registry.load_skill(
            echo_skill(), tick=lambda dt: seen.append(dt) or {"ticked": dt}
        )
        results = registry.tick_all(0.5)
        self.assertEqual(seen, [0.5])
        self.assertEqual(results["echo"], {"ticked": 0.5})

    def test_a_skill_can_route_to_another_mid_handling(self) -> None:
        registry = SkillRegistry()
        registry.load_skill(echo_skill())

        def forward(type, payload=None):
            reply = registry.route(
                SkillMessage("forwarder", "echo", "inner", {"from": "forwarder"})
            )
            return {"forwarded": reply.payload["value"]}

        registry.load_skill(
            Skill(
                id="forwarder",
                name="Forwarder",
                kind=SkillKind.COMMUNICATION,
                description="routes to echo",
                parameters=[SkillParameter("type", "Text")],
                output=SkillOutput("Record"),
                implementation=forward,
                status=SkillStatus.VALIDATED,
            )
        )
        reply = registry.route(SkillMessage("host", "forwarder", "outer"))
        self.assertEqual(reply.type, "result")
        self.assertEqual(
            reply.payload["value"], {"forwarded": {"type": "inner", "payload": {"from": "forwarder"}}}
        )


if __name__ == "__main__":
    unittest.main()