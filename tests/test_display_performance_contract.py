from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _manifest_for(output: Path, relative: str) -> tuple[str, dict]:
    page_path = output / relative
    html = page_path.read_text(encoding="utf-8")
    match = re.search(r'data-lazy-manifest="([^"]+)"', html)
    assert match, relative
    manifest_path = (page_path.parent / match.group(1)).resolve()
    return html, json.loads(manifest_path.read_text(encoding="utf-8"))


def test_large_pages_use_scoped_shards_and_keep_repository_boundaries(tmp_path):
    data_hash_before = _tree_hash(ROOT / "data")
    classic_hash_before = _tree_hash(ROOT / "docs" / "classic")
    output = tmp_path / "docs"
    subprocess.run(
        [sys.executable, "scripts/render_site.py", "--docs-dir", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert _tree_hash(ROOT / "data") == data_hash_before
    assert _tree_hash(ROOT / "docs" / "classic") == classic_hash_before
    assert not (output / "paper-index.json").exists()

    pages = (
        ("search/index.html", 500_000),
        ("working-papers/index.html", 750_000),
        ("topics/china/index.html", 750_000),
    )
    manifests: dict[str, dict] = {}
    for relative, size_limit in pages:
        html, manifest = _manifest_for(output, relative)
        manifests[relative] = manifest
        assert len(html.encode("utf-8")) <= size_limit, relative
        assert html.count('<article class="event"') <= 100, relative
        for match in re.finditer(r'<div class="lazy-initial">', html):
            block_start = match.end()
            block_end = html.find('<button class="control lazy-start"', block_start)
            assert block_end > block_start
            assert html[block_start:block_end].count('<article class="event"') <= 10, relative
        assert {"version", "count", "routed", "shards"} <= set(manifest), relative
        assert "html" not in manifest, relative
        assert len(json.dumps(manifest, ensure_ascii=False)) < 20_000, relative
        assert manifest["count"] >= 0, relative

    search_html = (output / "search" / "index.html").read_text(encoding="utf-8")
    assert 'data-lazy-defer="true"' in search_html
    assert 'class="control more-filters"' in search_html
    assert 'placeholder="搜索"' in search_html
    assert r"[\u3400-\u9fff]+" in search_html
    assert r"split(/\s+/)" in search_html
    search_manifest_url = re.search(r'data-lazy-manifest="([^"]+)"', search_html).group(1)
    search_dataset_id = re.search(r"/paper-index/([0-9a-f]{16})/manifest.json", search_manifest_url).group(1)
    shared_pages = [
        path
        for root_dir in ("topics", "fields", "journals")
        for path in (output / root_dir).rglob("index.html")
        if "data-lazy-filter=" in path.read_text(encoding="utf-8")
    ]
    for path in shared_pages[:3]:
        html = path.read_text(encoding="utf-8")
        dataset_id = re.search(r"/paper-index/([0-9a-f]{16})/manifest.json", html).group(1)
        assert dataset_id == search_dataset_id, path
    assert manifests["search/index.html"]["count"] >= manifests["working-papers/index.html"]["count"]
    assert manifests["search/index.html"]["count"] >= manifests["topics/china/index.html"]["count"]

    detail_shard = next((output / "paper-data").glob("*.json"))
    detail_items = json.loads(detail_shard.read_text(encoding="utf-8"))
    assert detail_items
    assert all("detected_time" in item for item in detail_items)

    for shard_path in (output / "paper-index").glob("*/shards/*.json"):
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        assert len(shard) <= 40
        assert all({"key", "search", "card"} <= set(item) for item in shard)
        assert all("html" not in item for item in shard)
        assert all("__BASE__" not in item["card"]["hr"] for item in shard)
        assert all(
            item["card"]["hr"].startswith("__PAPER_BASE__/paper/")
            and item["card"]["hr"].endswith("/")
            and "paper.html?key=" not in item["card"]["hr"]
            for item in shard
        )
        assert all({"p", "s", "a", "d", "u", "j", "tp", "cn", "dt", "dd", "dl", "od", "lg", "hr"} <= set(item["card"]) for item in shard)

    for dataset_dir in (output / "paper-index").iterdir():
        manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
        route_files = list((dataset_dir / "route").glob("*.json")) if (dataset_dir / "route").exists() else []
        if manifest.get("routed") is False:
            assert not route_files, f"{dataset_dir} should not emit route files"
        else:
            assert len(route_files) <= 256, f"{dataset_dir} has too many route files"

    for route_path in (output / "paper-index").glob("*/route/*.json"):
        route = json.loads(route_path.read_text(encoding="utf-8"))
        assert isinstance(route, dict)
        assert len(route) >= 1
        for shard_list in route.values():
            assert isinstance(shard_list, str)
            assert shard_list
            assert all(re.fullmatch(r"\d{4}", shard) for shard in shard_list.split(","))


def test_runtime_contract_defers_search_and_loads_only_requested_shards():
    renderer = (ROOT / "scripts" / "render_site.py").read_text(encoding="utf-8")
    assert "len(public) > 40" in renderer
    assert "LAZY_SHARD_SIZE = 40" in renderer
    assert "data-lazy-defer" in renderer
    assert "if (list.dataset.lazyDefer !== 'true' || hasPreset)" in renderer
    assert "route/" in renderer
    assert "shards/" in renderer
    assert "queryTokens" in renderer
    assert "ROUTE_BUCKETS" in renderer
    assert "ROUTE_SKIP_SHARD_LIMIT" in renderer
    assert "manifest.routed === false" in renderer
    assert "LAZY_LIST_SCRIPT = r" in renderer
    assert "renderCard" in renderer
    assert "escHtml" in renderer
    assert "card.dt" in renderer
    assert "card.dl" in renderer
    assert "missingDoi" in renderer
    assert "暂无 DOI" in renderer
    assert '文章链接</a><span class="doi">暂无 DOI</span>' in renderer
    assert "detected_label" in renderer
    assert "state.manifest = manifest" in renderer
    assert "more-filters" in renderer
    assert "details.more-filters[open]" in renderer
    assert "'Escape'" in renderer
    assert "max-height:70vh" in renderer
    assert "overflow-y:auto" in renderer
    assert "detected_time" in renderer
    assert "scoped_shared_events" in renderer
    assert "register_lazy_dataset" in renderer
    assert "data-lazy-filter" in renderer
    assert "presetTokenMatches" in renderer
    assert "routeBucket" in renderer
    assert "ROUTED_INITIAL_SHARDS" in renderer
    assert "loadNextRoutedShard" in renderer
    assert "String(bucket).padStart(3, '0')" in renderer
    assert "queryTokenList" in renderer
    assert "Math.min(start + 40, matches.length)" in renderer
    assert 'docs_dir / "paper-index.json"' not in renderer
