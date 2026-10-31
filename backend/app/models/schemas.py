"""Request bodies for the REST API (docs/05-api.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Mode = Literal["passive", "active"]
Audience = Literal["internal", "client"]


class InvestigationSettings(BaseModel):
    max_depth: int = Field(1, ge=0, le=3)
    connectors: list[str] = ["*"]
    exclude_connectors: list[str] = []
    # Quick scan cost tiers: "none" (free only), "metered" (+ monthly-quota
    # sources like Hunter/Tomba), "all" (+ paid ones like DeHashed).
    costly_connectors: Literal["none", "metered", "all"] = "all"


class InvestigationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=10, max_length=2000,
                         description="Why this investigation exists; recorded and audited.")
    mode: Mode = "passive"
    settings: InvestigationSettings = InvestigationSettings()
    retention_days: int | None = Field(None, ge=1, le=3650)


class InvestigationUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = Field(None, min_length=10, max_length=2000)
    mode: Mode | None = None
    settings: InvestigationSettings | None = None
    retention_days: int | None = Field(None, ge=1, le=3650)
    legal_hold: bool | None = None


class TargetCreate(BaseModel):
    value: str = Field(min_length=1, max_length=500)
    type: str = "auto"
    scan: bool = True
    max_depth: int | None = Field(None, ge=0, le=3)


class ScanRequest(BaseModel):
    entity_ids: list[str] | None = None      # default: every target of the investigation
    connectors: list[str] | None = None      # default: every applicable connector
    max_depth: int | None = Field(None, ge=0, le=3)


class PivotRequest(BaseModel):
    connectors: list[str] | None = None
    depth: int = Field(1, ge=1, le=3)


class StatusChange(BaseModel):
    reason: str = Field(min_length=3, max_length=1000,
                        description="e.g. the disambiguator that confirms or rules out a match")


class MergeRequest(BaseModel):
    ids: list[str] = Field(min_length=2)


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class ReportCreate(BaseModel):
    title: str | None = None
    body: dict[str, Any] | None = None       # omit to draft from the graph
    min_confidence: float = Field(0.5, ge=0, le=1)


class ReportUpdate(BaseModel):
    body: dict[str, Any]


class CollectRequest(BaseModel):
    goal: str = Field(min_length=5, max_length=2000,
                      description='e.g. "Map the attack surface and who is behind it"')
    target: str | None = Field(None, max_length=500,
                               description="added as a target first if it isn't one")
    max_tool_calls: int = Field(40, ge=1, le=150)
    # Default: every source, including monthly-quota (Hunter, Tomba) and paid
    # (DeHashed) ones; the ledger still enforces quotas and seed-only rules.
    costly_connectors: Literal["none", "metered", "all"] = "all"


class ReportAgentRequest(BaseModel):
    goal: str = Field(min_length=5, max_length=2000)
    max_tool_calls: int = Field(25, ge=0, le=100,
                                description="evidence captures it may take")
    # Claude first separates the subject from namesakes and noise (app/review.py),
    # and an independent reviewer fact-checks the report before it is final.
    verify: bool = True
    review: bool = True


class VerifyRequest(BaseModel):
    goal: str = Field("Separate the subject from namesakes and noise", min_length=5,
                      max_length=2000)
    max_tool_calls: int = Field(25, ge=0, le=100,
                                description="page reads, searches and persona look-ups")


class CaptureRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    entity_id: str | None = None


class DeepRequest(BaseModel):
    goal: str = Field(min_length=5, max_length=2000)
    target: str | None = Field(None, max_length=500)
    max_tool_calls: int = Field(60, ge=6, le=250,
                                description="lookups shared by all workers")
    costly_connectors: Literal["none", "metered", "all"] = "all"


class MaterialText(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200_000)


class MaterialLink(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
