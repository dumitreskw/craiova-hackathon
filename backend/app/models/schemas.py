"""Request bodies for the REST API (docs/05-api.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# GDPR Art. 6(1) lawful bases.
LegalBasis = Literal["consent", "contract", "legal_obligation", "vital_interests",
                     "public_task", "legitimate_interest"]
Mode = Literal["passive", "active"]
Audience = Literal["internal", "client"]


class InvestigationSettings(BaseModel):
    max_depth: int = Field(1, ge=0, le=3)
    connectors: list[str] = ["*"]
    exclude_connectors: list[str] = []
    # Quick scan cost tiers: "none" (free only), "metered" (+ monthly-quota
    # sources like Hunter/Tomba), "all" (+ paid ones like DeHashed).
    costly_connectors: Literal["none", "metered", "all"] = "none"


class InvestigationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=10, max_length=2000,
                         description="Why this investigation exists; recorded and audited.")
    legal_basis: LegalBasis
    mode: Mode = "passive"
    scope: list[str] = Field(default_factory=list,
                             description="Hosts/domains/IPs/CIDRs authorized for ACTIVE connectors.")
    authorization: str | None = Field(
        None, max_length=2000,
        description="Who authorized active work, when, and the reference document.")
    settings: InvestigationSettings = InvestigationSettings()
    retention_days: int | None = Field(None, ge=1, le=3650)

    @model_validator(mode="after")
    def _active_needs_authorization(self):
        _check_active(self.mode, self.scope, self.authorization)
        return self


class InvestigationUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = Field(None, min_length=10, max_length=2000)
    legal_basis: LegalBasis | None = None
    mode: Mode | None = None
    scope: list[str] | None = None
    authorization: str | None = None
    settings: InvestigationSettings | None = None
    retention_days: int | None = Field(None, ge=1, le=3650)
    legal_hold: bool | None = None


def _check_active(mode: str | None, scope: list[str] | None, authorization: str | None) -> None:
    if mode == "active":
        if not (authorization or "").strip():
            raise ValueError("active mode requires an authorization record")
        if not scope:
            raise ValueError("active mode requires at least one in-scope host")


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
    # The agent picks sources by need, so metered ones are fine by default;
    # paid ones (DeHashed) only when asked.
    costly_connectors: Literal["none", "metered", "all"] = "metered"


class ReportAgentRequest(BaseModel):
    goal: str = Field(min_length=5, max_length=2000)
    max_tool_calls: int = Field(25, ge=0, le=100,
                                description="evidence captures it may take")


class CaptureRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    entity_id: str | None = None
