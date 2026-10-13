"""
CyBreach Module 4: Arena - Hash Checker Service

Content-addressed hash-pinning and hash-mismatch detection for Module 1 playbooks.
Enforces zero interdependency by relying on static JSON fixtures and mock registries.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.campaign.models import CampaignDefinition, validate_sha256_hash

logger = logging.getLogger(__name__)


def compute_sha256(content: str | bytes) -> str:
    """Compute standard SHA-256 string prefixed with 'sha256:'."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256:{digest}"


def compute_canonical_content_hash(data: Dict[str, Any]) -> str:
    """
    Compute a deterministic content hash of a campaign definition.
    Excludes dynamic or ephemeral fields (e.g. hash_mismatch, needs_review, content_hash).
    """
    excluded_fields = {
        "content_hash",
        "hash_mismatch",
        "needs_review",
        "mismatched_playbooks",
        "difficulty_rating",
    }
    canonical_dict = {}
    for key, value in sorted(data.items()):
        if key in excluded_fields:
            continue
        if isinstance(value, list) and all(isinstance(x, dict) for x in value):
            canonical_dict[key] = [
                {k: v for k, v in sorted(item.items()) if k not in excluded_fields}
                for item in value
            ]
        else:
            canonical_dict[key] = value

    canonical_json = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
    return compute_sha256(canonical_json)


class Module1PlaybookRegistry:
    """
    Standalone registry representing Module 1 playbook metadata.
    Provides content-addressed hash lookups and simulates upstream hash updates
    without importing code or connecting to live Module 1 services.
    """

    def __init__(self, initial_playbooks: Optional[Dict[str, Dict[str, Any]]] = None):
        # Maps playbook_id -> playbook metadata dictionary
        self._by_id: Dict[str, Dict[str, Any]] = {}
        # Maps sha256_hash -> playbook_id
        self._by_hash: Dict[str, str] = {}

        if initial_playbooks:
            for pid, meta in initial_playbooks.items():
                self.register_playbook(
                    playbook_id=pid,
                    sha256_hash=meta["sha256_hash"],
                    name=meta.get("name", pid),
                    description=meta.get("description", ""),
                    techniques=meta.get("techniques", []),
                )

    def register_playbook(
        self,
        playbook_id: str,
        sha256_hash: str,
        name: str = "",
        description: str = "",
        techniques: Optional[List[str]] = None,
    ) -> None:
        """Register or update a playbook in the local catalog."""
        clean_hash = validate_sha256_hash(sha256_hash)
        # If hash previously pointed to another ID, clean up
        self._by_hash[clean_hash] = playbook_id
        self._by_id[playbook_id] = {
            "playbook_id": playbook_id,
            "sha256_hash": clean_hash,
            "name": name or playbook_id,
            "description": description,
            "techniques": techniques or [],
        }

    def get_by_hash(self, sha256_hash: str) -> Optional[Dict[str, Any]]:
        """Retrieve playbook metadata by its SHA-256 hash."""
        clean_hash = sha256_hash.strip().lower()
        pid = self._by_hash.get(clean_hash)
        if not pid:
            return None
        return self._by_id.get(pid)

    def get_current_hash_for_playbook_id(self, playbook_id: str) -> Optional[str]:
        """Get the current live hash for a given playbook ID."""
        meta = self._by_id.get(playbook_id)
        if not meta:
            return None
        return meta.get("sha256_hash")

    def update_playbook_hash(self, playbook_id: str, new_hash: str) -> None:
        """
        Simulate an upstream Module 1 playbook change by updating its hash.
        Used to test hash-mismatch detection and review flagging.
        """
        clean_new = validate_sha256_hash(new_hash)
        meta = self._by_id.get(playbook_id)
        if not meta:
            raise KeyError(f"Playbook '{playbook_id}' not found in registry")
        old_hash = meta["sha256_hash"]
        if old_hash in self._by_hash:
            del self._by_hash[old_hash]
        meta["sha256_hash"] = clean_new
        self._by_hash[clean_new] = playbook_id
        logger.info(
            "Updated playbook '%s' hash from %s to %s",
            playbook_id,
            old_hash,
            clean_new,
        )

    @classmethod
    def load_from_fixture(cls, fixture_path: Path | str) -> Module1PlaybookRegistry:
        """Load playbook metadata from a JSON fixture file."""
        path = Path(fixture_path)
        if not path.exists():
            raise FileNotFoundError(f"Fixture file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        registry = cls()
        if isinstance(data, list):
            for item in data:
                registry.register_playbook(
                    playbook_id=item["playbook_id"],
                    sha256_hash=item["sha256_hash"],
                    name=item.get("name", item["playbook_id"]),
                    description=item.get("description", ""),
                    techniques=item.get("techniques", []),
                )
        elif isinstance(data, dict):
            for pid, item in data.items():
                registry.register_playbook(
                    playbook_id=pid,
                    sha256_hash=item.get("sha256_hash", pid),
                    name=item.get("name", pid),
                    description=item.get("description", ""),
                    techniques=item.get("techniques", []),
                )
        return registry


def verify_campaign_hashes(
    campaign: CampaignDefinition,
    registry: Module1PlaybookRegistry,
) -> CampaignDefinition:
    """
    Check if pinned playbook hashes in a campaign match current Module 1 hashes.
    If any hash is unknown or changed, flags campaign.hash_mismatch = True and
    campaign.needs_review = True.
    """
    mismatches: List[Dict[str, Any]] = []

    hashes_to_check = list(campaign.playbook_hashes)
    for stage in campaign.stages:
        for sh in stage.playbook_hashes:
            if sh not in hashes_to_check:
                hashes_to_check.append(sh)

    for pinned_hash in hashes_to_check:
        playbook_meta = registry.get_by_hash(pinned_hash)
        if not playbook_meta:
            mismatches.append({
                "pinned_hash": pinned_hash,
                "status": "missing_or_modified",
                "message": "Pinned hash does not exist in active Module 1 playbook catalog",
            })

    if mismatches:
        campaign.hash_mismatch = True
        campaign.needs_review = True
        campaign.mismatched_playbooks = mismatches
    else:
        campaign.hash_mismatch = False
        campaign.needs_review = False
        campaign.mismatched_playbooks = []

    return campaign


def detect_affected_campaigns(
    playbook_id: str,
    old_hash: str,
    new_hash: str,
    campaigns: List[CampaignDefinition],
) -> List[CampaignDefinition]:
    """
    Given a changed playbook hash event (from mod1.playbook topic),
    find all campaigns referencing the old hash, flag them as mismatched,
    and return the affected campaigns.
    """
    old_clean = validate_sha256_hash(old_hash)
    new_clean = validate_sha256_hash(new_hash)
    affected: List[CampaignDefinition] = []

    for camp in campaigns:
        referenced_hashes = set(camp.playbook_hashes)
        for stage in camp.stages:
            referenced_hashes.update(stage.playbook_hashes)

        if old_clean in referenced_hashes:
            camp.hash_mismatch = True
            camp.needs_review = True
            camp.mismatched_playbooks.append({
                "playbook_id": playbook_id,
                "pinned_hash": old_clean,
                "current_hash": new_clean,
                "status": "upstream_hash_changed",
                "message": (
                    f"Underlying playbook '{playbook_id}' updated from {old_clean[:18]}... "
                    f"to {new_clean[:18]}..."
                ),
            })
            affected.append(camp)

    return affected
