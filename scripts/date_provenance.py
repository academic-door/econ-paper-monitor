"""Public wording for a record's date and where that date came from.

``PRODUCT.md`` is explicit about this:

    Official dates are evidence, not decoration.
    Do not call issue dates or Crossref fallback dates "online dates" unless
    the evidence supports it.

The Daily Door homepage currently prints every date as ``官方日期`` regardless
of provenance, while 824 records carry a Crossref registry date, 19 a volume
date, and 211 no official date at all. This module keeps the wording honest and
names the evidence, so the ``查看来源与日期`` panel actually discloses a source.

Display line only: it derives wording from fields the canonical record already
carries and never reads or writes ``data/**``.
"""

from __future__ import annotations

from typing import Any

# Evidence tiers, strongest first.
OFFICIAL = "official"
REGISTRY = "registry"
ISSUE = "issue"
UNKNOWN = "unknown"

DATE_KIND_LABELS = {
    OFFICIAL: "官方在线日期",
    REGISTRY: "登记日期",
    ISSUE: "卷期日期",
    UNKNOWN: "日期",
}

# Prefix or exact ``date_source`` values, mapped to the label shown to readers.
# Keep in step with render_site.date_source_label so both surfaces agree.
_SOURCE_RULES: tuple[tuple[str, str, str], ...] = (
    ("cnki_rss", "CNKI RSS", OFFICIAL),
    ("rss_", "官方 RSS", OFFICIAL),
    ("publisher", "出版社页面", OFFICIAL),
    ("elsevier_article_api", "出版社 API", OFFICIAL),
    ("world_bank_detail_api", "出版社 API", OFFICIAL),
    ("aea_forthcoming", "AEA 待刊列表", OFFICIAL),
    ("cepr_published_time", "CEPR 页面", OFFICIAL),
    ("iza_detail_month", "出版社页面", ISSUE),
    ("crossref_issue", "Crossref 卷期", ISSUE),
    ("crossref", "Crossref", REGISTRY),
    ("openalex", "OpenAlex", REGISTRY),
    ("nep_", "RePEc NEP", ISSUE),
    ("pdf", "PDF 原文", OFFICIAL),
)


def _date_source(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    return str(record.get("date_source") or "").strip().casefold()


def date_source_label(record: Any) -> str:
    """Name the evidence behind the record's date."""
    value = _date_source(record)
    if not value or value == "unknown":
        return "未标注"
    for prefix, label, _kind in _SOURCE_RULES:
        if value.startswith(prefix) or value == prefix:
            return label
    if "detail" in value:
        return "出版社页面"
    return "未标注"


def date_kind(record: Any) -> str:
    """Classify how strong the date evidence is."""
    value = _date_source(record)
    if not value or value == "unknown":
        return UNKNOWN
    for prefix, _label, kind in _SOURCE_RULES:
        if value.startswith(prefix) or value == prefix:
            return kind
    if "detail" in value:
        return OFFICIAL
    return UNKNOWN


def date_kind_label(record: Any) -> str:
    return DATE_KIND_LABELS[date_kind(record)]


def provenance_text(record: Any, official: str) -> str:
    """Return the reader-facing date line: what the date is, and its source.

    ``official`` is passed in rather than re-derived so the caller keeps
    ownership of which field wins.
    """
    if not official:
        return "官方日期暂未获取"
    return f"{date_kind_label(record)}：{official} · 来源：{date_source_label(record)}"
