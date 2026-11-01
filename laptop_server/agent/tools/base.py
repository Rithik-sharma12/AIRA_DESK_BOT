"""Tool framework for AIRA's task automation.

A :class:`Tool` is one thing AIRA can do on the laptop. Every tool declares:

- a JSON-schema for its arguments (sent to the model so it can call the tool),
- a risk level: ``safe`` tools run immediately, ``confirm`` tools only run after the
  user says yes (the agent engine handles that, never the model),
- a kind: ``action`` tools change something and already say what they did, ``info``
  tools return facts that the model then phrases as an answer.

Arguments are validated here before any handler runs, so a confused or manipulated
model can never pass unexpected values to the operating-system layer.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger("aira.tools")

Risk = Literal["safe", "confirm"]
Kind = Literal["action", "info"]


@dataclass
class ToolResult:
    ok: bool
    say: str                                   # short sentence AIRA can speak as-is
    data: dict[str, Any] = field(default_factory=dict)

    def for_model(self) -> dict[str, Any]:
        return {"ok": self.ok, "result": self.say, **({"data": self.data} if self.data else {})}


Handler = Callable[[dict[str, Any], str], Awaitable[ToolResult]]   # (args, lang) -> result


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    risk: Risk = "safe"
    kind: Kind = "action"
    confirm_prompt: Callable[[dict[str, Any], str], str] | None = None
    timeout_s: float = 15.0

    def spec(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}

    def prompt_for_confirmation(self, args: dict[str, Any], lang: str) -> str:
        if self.confirm_prompt:
            return self.confirm_prompt(args, lang)
        return t(lang, f"Shall I run {self.name}? Say yes to confirm.", f"{self.name} செய்யட்டுமா? சரி என்று சொல்லுங்கள்.")


class ToolArgumentError(ValueError):
    pass


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Small JSON-schema subset validator: object/properties/required/type/enum/min/max/maxLength."""
    if not isinstance(args, dict):
        raise ToolArgumentError("arguments must be an object")
    props: dict[str, Any] = schema.get("properties", {})
    clean: dict[str, Any] = {}
    for key, value in args.items():
        if key not in props:
            continue  # ignore extras the model invented
        rule = props[key]
        typ = rule.get("type")
        if typ == "integer":
            if isinstance(value, bool):
                raise ToolArgumentError(f"{key} must be a number")
            try:
                value = int(float(value))
            except (TypeError, ValueError) as exc:
                raise ToolArgumentError(f"{key} must be a number") from exc
        elif typ == "number":
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ToolArgumentError(f"{key} must be a number") from exc
        elif typ == "string":
            if not isinstance(value, str):
                value = str(value)
            value = value.strip()
            if len(value) > rule.get("maxLength", 200):
                raise ToolArgumentError(f"{key} is too long")
        elif typ == "boolean":
            value = value if isinstance(value, bool) else str(value).lower() in {"true", "1", "yes"}
        if "enum" in rule:
            if isinstance(value, str):
                value = value.lower()
            if value not in rule["enum"]:
                raise ToolArgumentError(f"{key} must be one of {', '.join(map(str, rule['enum']))}")
        if "minimum" in rule and value < rule["minimum"]:
            raise ToolArgumentError(f"{key} must be at least {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            raise ToolArgumentError(f"{key} must be at most {rule['maximum']}")
        clean[key] = value
    missing = [k for k in schema.get("required", []) if k not in clean or clean[k] in ("", None)]
    if missing:
        raise ToolArgumentError("missing " + ", ".join(missing))
    return clean


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]

    async def run(self, name: str, args: dict[str, Any], lang: str = "en") -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, t(lang, "I don't have a skill for that yet.", "அந்த வேலை இன்னும் எனக்குத் தெரியாது."))
        try:
            clean = validate_args(tool.parameters, args)
        except ToolArgumentError as exc:
            log.info("tool %s rejected arguments: %s", name, exc)
            return ToolResult(False, f"I couldn't do that: {exc}.")
        try:
            result = await asyncio.wait_for(tool.handler(clean, lang), timeout=tool.timeout_s)
        except asyncio.TimeoutError:  # same as TimeoutError on 3.11+, distinct on 3.10
            result = ToolResult(False, t(lang, "That took too long, so I stopped.", "ரொம்ப நேரம் ஆச்சு, நிறுத்திட்டேன்."))
        except Exception as exc:  # a tool must never crash the conversation
            log.exception("tool %s failed", name)
            result = ToolResult(False, f"Sorry, that didn't work ({exc.__class__.__name__}).")
        log.info("tool %s ok=%s", name, result.ok, extra={"data": {"event": "tool", "tool": name, "ok": result.ok}})
        return result


def t(lang: str, en: str, ta: str) -> str:
    """Pick the English or Tamil wording of a fixed reply."""
    return ta if lang == "ta" else en
