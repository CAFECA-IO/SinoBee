"""Provider-neutral message and tool types used throughout SinoBee."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Union


@dataclass
class TextPart:
    text: str
    type: str = "text"
    volatile: bool = False      # rebuilt for every request (the context tail): never cached, never stored


@dataclass
class ImagePart:
    data: str  # base64 (no data: prefix)
    media_type: str = "image/png"
    type: str = "image"

    @classmethod
    def from_bytes(cls, raw: bytes, media_type: str = "image/png") -> "ImagePart":
        return cls(base64.b64encode(raw).decode(), media_type)


Part = Union[TextPart, ImagePart]
Content = Union[str, list]  # str or list[Part]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    extra: dict | None = None  # provider-specific opaque data to echo back (e.g. Gemini thought signatures)


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: Content = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # tool name for role=tool

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "\n".join(p.text for p in self.content if isinstance(p, TextPart))

    def has_images(self) -> bool:
        return isinstance(self.content, list) and any(isinstance(p, ImagePart) for p in self.content)

    # ---- (de)serialisation for persistence ----
    def to_dict(self) -> dict:
        if isinstance(self.content, str):
            content: Any = self.content
        else:
            content = [
                {"type": "text", "text": p.text} if isinstance(p, TextPart)
                else {"type": "image", "media_type": p.media_type, "omitted": True}
                for p in self.content
            ]
        return {
            "role": self.role,
            "content": content,
            "tool_calls": [{"id": t.id, "name": t.name, "arguments": t.arguments,
                            **({"extra": t.extra} if t.extra else {})} for t in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        content = d.get("content", "")
        if isinstance(content, list):
            parts: list = []
            for p in content:
                if p.get("type") == "text":
                    parts.append(TextPart(p["text"]))
                else:  # images are not persisted
                    parts.append(TextPart("[image omitted]"))
            content = parts
        return cls(
            role=d["role"],
            content=content,
            tool_calls=[ToolCall(t["id"], t["name"], t.get("arguments") or {}, t.get("extra"))
                        for t in d.get("tool_calls") or []],
            tool_call_id=d.get("tool_call_id"),
            name=d.get("name"),
        )


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0  # input_tokens is the total prompt size, including cached/cache-write tokens
    thinking_tokens: int = 0     # part of output_tokens the model spent thinking (hidden), when the vendor says

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.thinking_tokens += getattr(other, "thinking_tokens", 0) or 0


@dataclass
class ChatResponse:
    message: Message
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""
    model: str = ""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema

    def json_schema(self) -> dict:
        return self.parameters


@dataclass
class ToolResult:
    content: Content
    is_error: bool = False

    @classmethod
    def ok(cls, value: Any) -> "ToolResult":
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, indent=2)
        return cls(str(value))

    @classmethod
    def error(cls, msg: str) -> "ToolResult":
        return cls(f"ERROR: {msg}", True)
