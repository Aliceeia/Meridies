"""Review of memories that someone asked to share with the community.  ** STUB **

Nothing here calls a third-party service. Sharing is a REQUEST: the memory stays private
(visibility "private", share_request.status "pending_review") until a reviewer approves it.

Interface (what a real implementation must provide):

    review_share_request(audio_path, meta) -> dict
        returns {"status": "pending_review" | "approved" | "rejected",
                 "transcript": str | None,
                 "flags": [ {"kind": "phone_number" | "street_address" | "third_party_voice" | "name"
                                     | "profanity" | "other", "detail": str, "start_s": float | None} ],
                 "reviewer": "auto" | "human" | "stub",
                 "notes": str}

The stub never approves: it always returns pending_review, so nothing reaches the community
until a real reviewer exists. Decisions the team still has to make are listed in TODO below.

TODO (decide before turning this on):
  1. Speech-to-text provider (on-device vs. cloud; which vendor; where audio may be sent).
  2. PII detection: phone numbers, street addresses, names; what to do on a hit (reject / bleep / ask author).
  3. Third-party voice detection (someone else speaking in the clip): diarisation provider, consent rule.
  4. Human review: who reviews, SLA, how the author is told the result (spoken notification?).
  5. Retention: how long pending/rejected audio is kept; deletion on author request.
  6. Listener reports (memory.reports is reserved): threshold that pulls a community memory back to review.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def transcribe(audio_path: Path) -> str | None:
    """STUB: speech-to-text. Returns None until a provider is chosen."""
    return None


def detect_sensitive(transcript: str | None, audio_path: Path) -> list[dict]:
    """STUB: phone numbers, addresses, other people's voices... Returns no flags until implemented."""
    return []


def review_share_request(audio_path: Path, meta: dict) -> dict:
    transcript = transcribe(audio_path)
    flags = detect_sensitive(transcript, audio_path)
    return {
        "status": "pending_review",          # the stub never auto-approves
        "transcript": transcript,
        "flags": flags,
        "reviewer": "stub",
        "notes": "No reviewer configured yet; stays private until a human approves.",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
