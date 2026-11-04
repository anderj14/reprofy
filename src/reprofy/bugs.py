"""Persistent bug records, independent of the storage implementation."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


BugStatus = Literal[
    "new", "classified", "repro_written", "fix_proposed", "fix_verified",
    "applied", "rejected", "failed", "auto_running", "branch_ready",
    "needs_human_review",
]
BUG_STATUSES = (
    "new", "classified", "repro_written", "fix_proposed", "fix_verified",
    "applied", "rejected", "failed", "auto_running", "branch_ready",
    "needs_human_review",
)
TRANSITIONS = {
    "new": {"classified", "failed", "auto_running"},
    "classified": {"repro_written", "failed"},
    "repro_written": {"fix_proposed", "failed"},
    "fix_proposed": {"fix_verified", "failed"},
    "fix_verified": {"applied", "rejected", "failed", "branch_ready"},
    "auto_running": {"new", "branch_ready", "needs_human_review", "failed"},
    "needs_human_review": {"new", "auto_running", "failed"},
    "branch_ready": {"applied", "failed"},
    "applied": set(), "rejected": set(), "failed": {"needs_human_review"},
}


class BugReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    text: str = Field(min_length=1, max_length=16_000)
    expected: str | None = Field(default=None, max_length=8_000)
    actual: str | None = Field(default=None, max_length=8_000)
    context: str | None = Field(default=None, max_length=16_000)

    @field_validator("text", "expected", "actual", "context")
    @classmethod
    def meaningful_text(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("Report fields must not be blank")
        return value.strip()


class BugClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    severity: Literal["low", "medium", "high", "critical"]
    area: str
    suspected_files: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class BugReproduction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    test_file: str
    fails_as_expected: bool = False
    expected_failure: str | None = None
    attempts: int = Field(default=0, ge=0, le=3, strict=True)
    validation: Literal["accepted", "rejected", "needs_clarification"] | None = None
    review_reason: str | None = Field(default=None, max_length=2_000)
    observed_cause: str | None = Field(default=None, max_length=2_000)
    evidence_file: str | None = None


class BugFix(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    attempts: int = Field(default=0, ge=0)
    diff_file: str | None = None
    proposal_file: str | None = None
    tests_pass: bool = False
    branch_name: str | None = None
    commit_sha: str | None = None


class Bug(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    id: str = Field(pattern=r"^BUG-[0-9]{3,}$", max_length=64)
    created_at: AwareDatetime
    revision: int = Field(default=0, ge=0, strict=True)
    status: BugStatus = "new"
    report: BugReport
    classification: BugClassification | None = None
    repro: BugReproduction | None = None
    fix: BugFix | None = None
    failure: str | None = Field(default=None, max_length=4000)

    @field_validator("id")
    @classmethod
    def canonical_id(cls, value: str) -> str:
        number = int(value[4:])
        if number < 1 or value != f"BUG-{number:03d}":
            raise ValueError("Bug ID must use canonical BUG-001 numbering")
        return value
