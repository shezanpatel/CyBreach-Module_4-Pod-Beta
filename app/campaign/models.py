"""
CyBreach Module 4: Arena - Campaign Models

Data models for content-addressed campaign catalog, multi-stage boss fights,
hash-pinning verification, and multi-axis filtering.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


SHA256_PATTERN = re.compile(r"^sha256:[a-fA-F0-9]{64}$")


class CampaignTier(str, Enum):
    QUICK_SCAN = "quick_scan"
    GUIDED = "guided"
    AUDIT = "audit"
    BOSS_FIGHT = "boss_fight"
    CONTINUOUS = "continuous"


class RegionCode(str, Enum):
    GLOBAL = "global"
    EU = "eu"
    NA = "na"
    APAC = "apac"
    LATAM = "latam"
    MEA = "mea"


class AssetCriticality(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RegulatoryFramework(str, Enum):
    NIST = "NIST"
    ISO27001 = "ISO27001"
    PCI_DSS = "PCI-DSS"
    GDPR = "GDPR"
    PDPA = "PDPA"
    LGPD = "LGPD"


class StageExecutionState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


def validate_sha256_hash(hash_value: str) -> str:
    """Validate that a hash string is a valid content-addressed SHA-256 reference."""
    if not isinstance(hash_value, str):
        raise ValueError("Playbook hash must be a string")
    clean = hash_value.strip()
    if not SHA256_PATTERN.match(clean):
        raise ValueError(
            f"Invalid content-addressed hash '{hash_value}'. Expected format: 'sha256:<64 hex characters>'"
        )
    return clean.lower()


class BossStage(BaseModel):
    """A distinct stage in a multi-stage boss-fight campaign."""
    stage_id: str = Field(..., description="Unique stage identifier")
    stage_number: int = Field(..., ge=1, description="Sequential stage order (1-indexed)")
    title: str = Field(..., description="Stage title")
    description: str = Field("", description="Narrative description of the stage")
    playbook_hashes: List[str] = Field(
        ...,
        description="Module 1 playbook SHA-256 content hashes pinned to this stage",
    )
    techniques: List[str] = Field(
        default_factory=list,
        description="MITRE ATT&CK technique IDs exercised in this stage",
    )
    prerequisite_stage_ids: List[str] = Field(
        default_factory=list,
        description="List of stage_ids that must be completed before this stage",
    )
    weight: float = Field(
        default=1.0,
        gt=0,
        description="Scoring weight for composite evaluation",
    )
    required_verdict: str = Field(
        default="Detected",
        description="Target outcome verdict required to pass stage (e.g. Detected)",
    )

    @field_validator("playbook_hashes")
    @classmethod
    def check_stage_playbook_hashes(cls, values: List[str]) -> List[str]:
        if not values:
            raise ValueError("Stage must include at least one pinned playbook hash")
        return [validate_sha256_hash(h) for h in values]


class DifficultyRating(BaseModel):
    """Difficulty metric derived from technique breadth and asset criticality."""
    score: float = Field(..., description="Numeric difficulty score from 1.0 to 100.0")
    level: str = Field(..., description="Descriptive difficulty level: Easy, Medium, Hard, Boss")
    technique_count: int = Field(..., description="Number of distinct techniques")
    asset_criticality: str = Field(..., description="Asset criticality weight used")
    stage_count: int = Field(default=1, description="Number of execution stages")


class CampaignDefinition(BaseModel):
    """Content-addressed campaign definition pinned to Module 1 playbooks."""
    campaign_id: str = Field(..., description="Unique campaign identifier")
    title: str = Field(..., description="Human-readable title")
    description: str = Field("", description="Detailed narrative description")
    playbook_hashes: List[str] = Field(
        ...,
        description="Module 1 playbook hashes pinned to this campaign",
    )
    tier: str = Field(..., description="Campaign tier: quick_scan, guided, audit, boss_fight, continuous")
    regions: List[str] = Field(
        default_factory=lambda: ["global"],
        description="Geographic applicability codes: global, eu, na, apac, latam, mea",
    )
    techniques: List[str] = Field(
        default_factory=list,
        description="MITRE ATT&CK technique IDs covered by this campaign",
    )
    regulations: List[str] = Field(
        default_factory=list,
        description="Compliance frameworks: NIST, ISO27001, PCI-DSS, GDPR, PDPA, LGPD",
    )
    credit_cost: int = Field(..., ge=0, description="Cost in platform credits to execute")
    asset_criticality: str = Field(
        default="medium",
        description="Criticality of target environment: low, medium, high, critical",
    )
    estimated_score_impact: float = Field(
        default=5.0,
        ge=0.0,
        description="Estimated resilience score delta upon successful completion",
    )
    stages: List[BossStage] = Field(
        default_factory=list,
        description="Ordered stages for multi-stage boss-fight campaigns",
    )
    content_hash: Optional[str] = Field(
        default=None,
        description="SHA-256 hash of this definition content for immutability check",
    )
    hash_mismatch: bool = Field(
        default=False,
        description="Flag indicating if any underlying Module 1 playbook hashes changed",
    )
    needs_review: bool = Field(
        default=False,
        description="Flag indicating campaign requires operator review due to upstream drift",
    )
    mismatched_playbooks: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Details of any mismatched playbook hashes",
    )
    difficulty_rating: Optional[DifficultyRating] = Field(
        default=None,
        description="Calculated difficulty metric based on technique breadth and criticality",
    )
    version: int = Field(default=1, ge=1, description="Campaign schema version")
    archived: bool = Field(default=False, description="Archive flag for superseded versions")

    @field_validator("playbook_hashes")
    @classmethod
    def check_playbook_hashes(cls, values: List[str]) -> List[str]:
        if not values:
            raise ValueError("Campaign must specify at least one pinned playbook hash")
        return [validate_sha256_hash(h) for h in values]

    def compute_difficulty(self) -> DifficultyRating:
        """
        Calculate campaign difficulty rating based on technique breadth,
        asset criticality, and multi-stage complexity.
        """
        all_techniques = set(self.techniques)
        for stage in self.stages:
            all_techniques.update(stage.techniques)
        technique_count = max(len(all_techniques), 1)

        criticality_multipliers = {
            AssetCriticality.LOW.value: 1.0,
            AssetCriticality.MEDIUM.value: 1.5,
            AssetCriticality.HIGH.value: 2.2,
            AssetCriticality.CRITICAL.value: 3.0,
        }
        crit_mult = criticality_multipliers.get(self.asset_criticality.lower(), 1.5)

        stage_count = max(len(self.stages), 1)
        stage_mult = 1.0 + (0.25 * (stage_count - 1))

        raw_score = (technique_count * 2.5) * crit_mult * stage_mult
        score = min(max(round(raw_score, 1), 1.0), 100.0)

        if score < 15.0:
            level = "Easy"
        elif score < 35.0:
            level = "Medium"
        elif score < 65.0:
            level = "Hard"
        else:
            level = "Boss"

        rating = DifficultyRating(
            score=score,
            level=level,
            technique_count=technique_count,
            asset_criticality=self.asset_criticality,
            stage_count=stage_count,
        )
        self.difficulty_rating = rating
        return rating


class BossFightStageResult(BaseModel):
    """Result record for an individual boss-fight stage."""
    stage_id: str
    stage_number: int
    verdict: str = Field(..., description="Observed outcome verdict: Detected, Missed, Partial, NoData")
    state: StageExecutionState
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    passed: bool
    evidence_ref: Optional[str] = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BossFightSession(BaseModel):
    """
    Session tracker enforcing multi-stage boss-fight campaign logic.
    A boss fight is only complete when all stages are finished in sequence.
    """
    session_id: str
    campaign_id: str
    tenant_id: str
    current_stage_number: int = 1
    total_stages: int
    stage_results: Dict[str, BossFightStageResult] = Field(default_factory=dict)
    is_completed: bool = False
    all_stages_passed: bool = False
    composite_score: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    def submit_stage(
        self,
        stage: BossStage,
        verdict: str,
        score: float,
        evidence_ref: Optional[str] = None,
    ) -> BossFightStageResult:
        """
        Record result for a boss fight stage, validating prerequisite completion.
        """
        if self.is_completed:
            raise ValueError("Boss fight session is already completed and finalized")

        if stage.stage_number != self.current_stage_number:
            raise ValueError(
                f"Cannot execute stage {stage.stage_number}. Expected current stage {self.current_stage_number}"
            )

        for prereq_id in stage.prerequisite_stage_ids:
            prereq_res = self.stage_results.get(prereq_id)
            if not prereq_res or not prereq_res.passed:
                raise ValueError(
                    f"Prerequisite stage '{prereq_id}' has not been successfully completed"
                )

        passed = (verdict.lower() == stage.required_verdict.lower())
        state = StageExecutionState.PASSED if passed else StageExecutionState.FAILED

        result = BossFightStageResult(
            stage_id=stage.stage_id,
            stage_number=stage.stage_number,
            verdict=verdict,
            state=state,
            score=score,
            passed=passed,
            evidence_ref=evidence_ref,
            executed_at=datetime.now(timezone.utc),
        )
        self.stage_results[stage.stage_id] = result

        if passed:
            if self.current_stage_number < self.total_stages:
                self.current_stage_number += 1
            else:
                self._finalize_completion(success=True)
        else:
            self._finalize_completion(success=False)

        return result

    def _finalize_completion(self, success: bool) -> None:
        """Finalize the boss fight session and compute composite score."""
        self.is_completed = True
        self.all_stages_passed = success
        self.completed_at = datetime.now(timezone.utc)

        if not self.stage_results:
            self.composite_score = 0.0
            return

        total_weight = 0.0
        weighted_score_sum = 0.0
        for res in self.stage_results.values():
            weighted_score_sum += res.score
            total_weight += 1.0

        if total_weight > 0:
            self.composite_score = round(weighted_score_sum / total_weight, 2)
        else:
            self.composite_score = 0.0
