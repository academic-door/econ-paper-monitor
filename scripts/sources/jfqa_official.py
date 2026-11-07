"""Parse first-party JFQA Forthcoming RSS full abstracts, never teasers.

WordPress <description> is often truncated; only content:encoded contains
an original publisher-provided abstract. Do not infer formal publication dates.
"""

from __future__ import annotations

from common import clean_abstract_text


def verified_forthcoming_abstract(encoded: str | None) -> str | None:
    """Return one full diamond-prefixed official article paragraph, if valid."""
    if not isinstance(encoded, str) or not encoded or len(encoded) > 200_000:
        return None

    candidates: list[str] = []
    for segment in encoded.split("<p")[1:]:
        if ">" not in segment:
            continue
        text_after_open = segment.split(">", 1)[1].lstrip()
        if not text_after_open.startswith("♦"):
            continue
        if "</p>" not in text_after_open:
            return None
        paragraph = text_after_open[1:].split("</p>", 1)[0]
        abstract = clean_abstract_text(paragraph)
        if not (120 <= len(abstract) <= 12_000):
            return None
        if abstract.endswith(("...", "…", "[…]", "[...]")):
            return None
        candidates.append(abstract)

    # Fail closed if the upstream article markup changes or is ambiguous.
    return candidates[0] if len(candidates) == 1 else None
