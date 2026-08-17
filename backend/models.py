from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

ALLOWED_TOOLS = (
    "read_only_shell",
    "search_channel_history",
    "remember",
    "recall",
)
DEFAULT_TOOLS = list(ALLOWED_TOOLS)
LEDGER_TOOLS = ["search_channel_history", "remember", "recall"]


def normalize_tools(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    unknown = [t for t in value if t not in ALLOWED_TOOLS]
    if unknown:
        raise ValueError(f"unknown tools: {unknown}")
    return list(dict.fromkeys(value))


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    topic: str = ""


class MessageCreate(BaseModel):
    author: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=8000)
    author_kind: str = "human"
    parent_id: int | None = None


class RegisterRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")


class ReactionCreate(BaseModel):
    author: str = Field(min_length=1, max_length=64)
    emoji: str = Field(min_length=1, max_length=8)


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_\-]+$")
    system_prompt: str = Field(min_length=1, max_length=4000)
    model: str = "llama-3.3-70b-versatile"
    channel_scope: str | None = None
    history_window: int = Field(default=12, ge=1, le=50)
    max_tool_calls: int = Field(default=3, ge=1, le=8)
    tools: list[str] | None = None

    @field_validator("tools")
    @classmethod
    def tools_ok(cls, value: list[str] | None) -> list[str] | None:
        return normalize_tools(value)


class AgentPatch(BaseModel):
    system_prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    channel_scope: str | None = None
    history_window: int | None = Field(default=None, ge=1, le=50)
    max_tool_calls: int | None = Field(default=None, ge=1, le=8)
    tools: list[str] | None = None

    @field_validator("tools")
    @classmethod
    def tools_ok(cls, value: list[str] | None) -> list[str] | None:
        return normalize_tools(value)
