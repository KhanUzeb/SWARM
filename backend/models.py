from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any

from .tools.registry import (
    BUILTIN_TOOL_NAMES,
    COMPOSIO_PLUGIN_TOOLS,
    DEFAULT_BUILTIN_TOOLS,
    LEDGER_BUILTIN_TOOLS,
)

ALLOWED_TOOLS = BUILTIN_TOOL_NAMES
DEFAULT_TOOLS = list(DEFAULT_BUILTIN_TOOLS) + list(COMPOSIO_PLUGIN_TOOLS)
LEDGER_TOOLS = LEDGER_BUILTIN_TOOLS
DEFAULT_JOB = "Teammate"
AGENT_STATUSES = ("idle", "working", "needs_approval")
HANDLE_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
MAX_GROUP_MEMBERS = 8
MAX_TEAM_MEMBERS = 8
USER_ROLES = ("admin", "member")


def slugify_handle(text: str, fallback: str = "bot") -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")[:32]
    return raw if HANDLE_RE.match(raw) else fallback


def pretty_name(handle: str) -> str:
    return (handle or "bot").replace("-", " ").replace("_", " ").strip().title() or "Bot"

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
    out: list[str] = []
    for t in value:
        name = (t or "").strip()
        if not name:
            continue
        if name not in out:
            out.append(name)
    return out


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    topic: str = ""
    kind: str = Field(default="room", pattern=r"^(room|group)$")
    members: list[str] = Field(default_factory=list, max_length=MAX_GROUP_MEMBERS)

    @field_validator("members")
    @classmethod
    def members_ok(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for raw in value:
            name = (raw or "").strip()
            if name and name not in out:
                out.append(name)
        return out[:MAX_GROUP_MEMBERS]


class DirectMessageCreate(BaseModel):
    handle: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    id: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=200)
    members: list[str] = Field(min_length=1, max_length=MAX_TEAM_MEMBERS)

    @model_validator(mode="after")
    def team_ok(self) -> TeamCreate:
        slug = (self.id or "").strip() or slugify_handle(self.name, "team")
        if not HANDLE_RE.match(slug) or not (1 <= len(slug) <= 32):
            raise ValueError("id must be 1-32 letters, numbers, _ or -")
        names: list[str] = []
        for raw in self.members:
            name = (raw or "").strip()
            if name and name not in names:
                names.append(name)
        if not names:
            raise ValueError("team needs at least one bot")
        return self.model_copy(update={"id": slug, "members": names[:MAX_TEAM_MEMBERS]})


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=200)
    members: list[str] | None = Field(default=None, max_length=MAX_TEAM_MEMBERS)

    @field_validator("members")
    @classmethod
    def members_ok(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        out: list[str] = []
        for raw in value:
            name = (raw or "").strip()
            if name and name not in out:
                out.append(name)
        return out[:MAX_TEAM_MEMBERS]


class MessageCreate(BaseModel):
    author: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=8000)
    author_kind: str = Field(default="human", pattern=r"^human$")
    parent_id: int | None = None


class CustomToolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    description: str = Field(min_length=1, max_length=500)
    parameters: dict[str, Any] | None = None
    handler_type: str = Field(default="template", pattern=r"^[a-z_]+$")
    handler_config: dict[str, Any] | None = None
    enabled: bool = True


class CustomToolPatch(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=500)
    parameters: dict[str, Any] | None = None
    handler_type: str | None = Field(default=None, pattern=r"^[a-z_]+$")
    handler_config: dict[str, Any] | None = None
    enabled: bool | None = None


class AiProviderConnect(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)
    model: str | None = Field(default=None, max_length=200)


class AiProviderModel(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class AiModelsPreview(BaseModel):
    api_key: str | None = Field(default=None, max_length=512)


class SystemRootSet(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class RegisterRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    password: str | None = Field(default=None, max_length=128)

    @field_validator("password")
    @classmethod
    def password_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ReactionCreate(BaseModel):
    author: str = Field(min_length=1, max_length=64)
    emoji: str = Field(min_length=1, max_length=8)


class AgentCreate(BaseModel):
    name: str = Field(default="", max_length=32)
    display_name: str = Field(default="", max_length=40)
    system_prompt: str = Field(min_length=1, max_length=4000)
    model: str = Field(default=DEFAULT_GROQ_MODEL, min_length=1, max_length=200)
    channel_scope: str | None = None
    history_window: int = Field(default=12, ge=1, le=50)
    max_tool_calls: int = Field(default=3, ge=1, le=8)
    tools: list[str] | None = None
    job: str = Field(default=DEFAULT_JOB, min_length=1, max_length=64)

    @model_validator(mode="after")
    def names_ok(self) -> AgentCreate:
        handle = (self.name or "").strip()
        display = (self.display_name or "").strip()
        if not handle and not display:
            raise ValueError("name is required")
        if not handle:
            handle = slugify_handle(display)
        if not HANDLE_RE.match(handle) or not (1 <= len(handle) <= 32):
            raise ValueError("name must be 1-32 letters, numbers, _ or -")
        if not display:
            display = pretty_name(handle)
        return self.model_copy(update={"name": handle, "display_name": display[:40]})

    @field_validator("model")
    @classmethod
    def model_ok(cls, value: str) -> str:
        return resolve_groq_model(value)

    @field_validator("tools")
    @classmethod
    def tools_ok(cls, value: list[str] | None) -> list[str] | None:
        return normalize_tools(value)


class AgentPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=40)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    model: str | None = Field(default=None, min_length=1, max_length=200)
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


class ComposioToolkitConnect(BaseModel):
    toolkit: str = Field(min_length=1, max_length=64)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    graph: dict[str, Any] = Field(default_factory=dict)


class WorkflowPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    graph: dict[str, Any] | None = None


class RunCreate(BaseModel):
    objective: str = Field(min_length=1, max_length=4000)
    workflow_id: str | None = Field(default=None, max_length=64)
    policy: str = Field(default="supervised", pattern=r"^(supervised|autonomous|checkpointed)$")
