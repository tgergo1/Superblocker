"""Cryptographic verification for professional and statutory plan artifacts."""

import hashlib
import hmac
import json
from typing import Any

from pydantic import BaseModel

from app.models.schemas import ApprovalArtifact, AssuranceEvidenceArtifact


def approval_signature_payload(artifact: BaseModel | dict[str, Any]) -> bytes:
    """Return a stable JSON payload excluding the signature itself."""
    if isinstance(artifact, BaseModel):
        data = artifact.model_dump(mode="json", exclude={"signature"}, exclude_none=True)
    else:
        data = {
            key: value
            for key, value in artifact.items()
            if key != "signature" and value is not None
        }
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return canonical.encode("utf-8")


def compute_approval_signature(
    artifact: BaseModel | dict[str, Any],
    secret: str,
) -> str:
    """Compute the HMAC-SHA256 signature for an approval artifact."""
    if len(secret) < 32:
        raise ValueError("approval signing secret must contain at least 32 characters")
    return hmac.new(
        secret.encode("utf-8"),
        approval_signature_payload(artifact),
        hashlib.sha256,
    ).hexdigest()


def verify_approval_artifact(artifact: ApprovalArtifact, secret: str) -> bool:
    """Verify that an artifact was issued by a configured trusted authority."""
    expected = compute_approval_signature(artifact, secret)
    return hmac.compare_digest(expected, artifact.signature)


def verify_evidence_artifact(artifact: AssuranceEvidenceArtifact, secret: str) -> bool:
    """Verify artifact signature and the canonical analytical payload digest."""
    payload_digest = hashlib.sha256(
        json.dumps(
            artifact.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    if not hmac.compare_digest(payload_digest, artifact.payload_sha256):
        return False
    expected = compute_approval_signature(artifact, secret)
    return hmac.compare_digest(expected, artifact.signature)
