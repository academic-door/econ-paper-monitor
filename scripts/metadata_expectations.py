"""Conservative expectations for metadata gaps on non-research journal content.

The classifier does not remove, relabel, or mutate public records. It only helps
quality metrics and retry work distinguish fields that are commonly absent by
content type from fields that should still be recovered from authoritative
sources.
"""

from __future__ import annotations

import re
from typing import Any


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def normalized_title(record: dict[str, Any]) -> str:
    title = _TAG_RE.sub(" ", str(record.get("title") or ""))
    return _SPACE_RE.sub(" ", title).strip()


def expected_missing_reason(record: dict[str, Any], field: str) -> str | None:
    """Return a conservative reason when a missing field is expected/optional.

    False negatives are preferred over false positives: a research record stays
    actionable unless its title strongly identifies correction/front-matter,
    editorial/acknowledgement, or review content.
    """
    if field not in {"abstract", "authors"}:
        raise ValueError(f"unsupported metadata field: {field}")

    title = normalized_title(record)
    lowered = title.casefold()
    journal = _SPACE_RE.sub(" ", str(record.get("journal") or "")).strip().casefold()

    # Correction notices commonly have no standalone abstract. Their authors
    # remain meaningful, so author gaps are not suppressed.
    if field == "abstract" and re.match(r"^(corrigendum|erratum)\b", lowered):
        return "correction_notice"
    if field == "abstract" and re.match(r"^correction(?:\s+to\b|\s*:)", lowered):
        return "correction_notice"

    # Narrow editorial/front-matter patterns observed in the current corpus.
    if lowered == "editorial data" or lowered.startswith("editorial: "):
        return "editorial"
    if "outstanding reviewer appreciation" in lowered:
        return "reviewer_acknowledgement"
    if re.match(r"^announcing\b.*\beditors?\b", lowered):
        return "editorial_announcement"

    # DOI-bearing issue front matter can look like a journal record. Require the
    # normalized title to begin with the journal name and contain both markers.
    if (
        journal
        and lowered.startswith(journal)
        and re.search(r"\bvolume\b", lowered)
        and re.search(r"\bnumber\b", lowered)
    ):
        return "issue_front_matter"

    # JEL book-review titles conventionally include the reviewed book's author.
    # The abstract is optional, but a missing reviewer author remains actionable.
    if field == "abstract" and journal == "journal of economic literature" and " by " in lowered:
        return "book_review"

    return None
