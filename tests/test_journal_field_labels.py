from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import render_site


def test_all_journal_registry_fields_have_public_labels() -> None:
    journals = render_site.load_journals(render_site.DATA_DIR / "journals.yml")
    fields = {field for journal in journals for field in (journal.get("fields") or [])}

    assert fields <= set(render_site.FIELD_LABELS)


def test_journal_field_aliases_render_in_chinese() -> None:
    assert render_site.field_label("international_trade") == "国际贸易"
    assert render_site.field_label("macro") == "宏观经济学"
    assert render_site.field_label("china") == "中国经济"
