from __future__ import annotations

from pydantic import BaseModel, Field


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
