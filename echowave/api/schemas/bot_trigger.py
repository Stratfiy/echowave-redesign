"""What a trigger looks like over the wire (KAN-137).

The operator writes a sentence; the compile endpoint answers with either a
plan or the questions standing in its way. Only a plan is saved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TriggerField(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)
    required: bool = False


class TriggerRule(BaseModel):
    field: str = Field(min_length=1, max_length=80)
    op: str = Field(min_length=2, max_length=8)
    value: str | None = Field(default=None, max_length=120)


class TriggerQuestion(BaseModel):
    field: str
    question: str


class TriggerCompileRequest(BaseModel):
    """A sentence, and the answers to whatever the last compile asked."""

    sentence: str = Field(min_length=1, max_length=2_000)
    answers: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class TriggerCompileResponse(BaseModel):
    name: str
    instruction: str
    fields: list[TriggerField] = []
    filter: list[TriggerRule] = []
    #: Non-empty means: answer these and compile again before saving.
    questions: list[TriggerQuestion] = []
    ready: bool = True
    #: Why the plan is what it is, when the model did not make it.
    note: str = ""
    #: The filter in words, for the preview.
    filter_summary: str = ""


class TriggerWrite(BaseModel):
    """Saving a compiled plan. ``is_active`` is its own endpoint."""

    name: str = Field(min_length=1, max_length=120)
    #: ``webhook`` (default) or ``email`` (KAN-138): what rings the bot.
    source: str = Field(default="webhook", max_length=16)
    sentence: str = Field(default="", max_length=2_000)
    instruction: str = Field(default="", max_length=4_000)
    fields: list[TriggerField] = Field(default_factory=list, max_length=20)
    filter: list[TriggerRule] = Field(default_factory=list, max_length=10)

    model_config = ConfigDict(extra="forbid")


class TriggerResponse(BaseModel):
    id: int
    workflow_id: int
    uuid: str
    name: str
    source: str
    sentence: str
    instruction: str
    fields: list[TriggerField] = []
    filter: list[TriggerRule] = []
    filter_summary: str = ""
    is_active: bool
    #: Where the sender posts, and what it must present. Shown once on the
    #: card with a copy button; there is no separate reveal endpoint.
    url: str
    secret: str
    #: For an email trigger: the address to forward mail to. None for a webhook.
    address: str | None = None
    last_fired_at: datetime | None = None
    fired_count: int = 0
    created_at: datetime | None = None


class TriggerListResponse(BaseModel):
    triggers: list[TriggerResponse] = []
    max_per_workflow: int


class TriggerTestRequest(BaseModel):
    """A sample event to fire the trigger with, right now."""

    payload: dict[str, Any] = Field(default_factory=dict)


class TriggerTestResponse(BaseModel):
    started: bool
    #: ``accepted`` (running), or ``filtered`` (the sample did not match).
    status: str
    detail: str = ""
    #: Required fields the sample did not carry -- what the bot will be told.
    missing_fields: list[str] = []
