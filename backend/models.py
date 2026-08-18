from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

ALLOWED_TOOLS = (
    "read_only_shell",
    "search_channel_history",
    "remember",
    "recall",
    "list_workspace",
    "write_workspace",
    "save_skill",
    "request_approval",
)
DEFAULT_TOOLS = list(ALLOWED_TOOLS)
LEDGER_TOOLS = ["search_channel_history", "remember", "recall"]
DEFAULT_JOB = "Teammate"
AGENT_STATUSES = ("idle", "working", "needs_approval")

# Groq shut down llama-3.1-8b-instant and llama-3.3-70b-versatile on
# 2026-08-16 for free/developer tiers.
# https://console.groq.com/docs/deprecations
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
FAST_GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_MODEL_ALIASES = {
    "llama-3.1-8b-instant": FAST_GROQ_MODEL,
    "llama-3.3-70b-versatile": DEFAULT_GROQ_MODEL,
    "llama-3.1-70b-versatile": DEFAULT_GROQ_MODEL,
}


def resolve_groq_model(model: str) -> str:
    return GROQ_MODEL_ALIASES.get(model, model)


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
    model: str = DEFAULT_GROQ_MODEL
    channel_scope: str | None = None
    history_window: int = Field(default=12, ge=1, le=50)
    max_tool_calls: int = Field(default=3, ge=1, le=8)
    tools: list[str] | None = None
    job: str = Field(default=DEFAULT_JOB, min_length=1, max_length=64)

    @field_validator("model")
    @classmethod
    def model_ok(cls, value: str) -> str:
        return resolve_groq_model(value)

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
    job: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("model")
    @classmethod
    def model_ok(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return resolve_groq_model(value)

    @field_validator("tools")
    @classmethod
    def tools_ok(cls, value: list[str] | None) -> list[str] | None:
        return normalize_tools(value)


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    body: str = Field(min_length=1, max_length=8000)


class SkillPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    body: str | None = Field(default=None, min_length=1, max_length=8000)


class RoutineCreate(BaseModel):
    agent_name: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=4000)
    interval_minutes: int = Field(default=60, ge=1, le=10080)
    enabled: bool = True


class RoutinePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    instructions: str | None = Field(default=None, min_length=1, max_length=4000)
    interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    enabled: bool | None = None


class ApprovalResolve(BaseModel):
    status: str = Field(pattern=r"^(approved|denied)$")
