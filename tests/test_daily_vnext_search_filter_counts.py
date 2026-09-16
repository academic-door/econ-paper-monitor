import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts" / "templates" / "daily_vnext.html"


def test_search_filter_badges_recompute_from_current_query():
    template = TEMPLATE.read_text(encoding="utf-8")

    assert (
        "const matchesQuery = (entry, query) => !query || normalize(entry.dataset.search).includes(query);"
        in template
    )
    assert "const countFor = (type, query = currentQuery()) => {" in template
    assert "if (type === 'all') return entries.filter((entry) => matchesQuery(entry, query)).length;" in template
    assert "entry.dataset.china === 'true' && matchesQuery(entry, query)" in template
    assert "entry.dataset.kind === type && matchesQuery(entry, query)" in template

    apply_block = re.search(r"const apply = \(\{animate = true, push = false\} = \{\}\) => \{.*?\n      \};\n      const readUrl", template, re.S)
    assert apply_block, "Daily vNext apply() block not found"
    assert "annotateButtons();" in apply_block.group(0)
