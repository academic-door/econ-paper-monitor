"""Issue #371: one-time, evidence-locked CNKI first-observation reconciliation.

Proof: local supplement commit f4168eb25aa7, data/seen.json blob
f838c7c834b9711d670d74ecb074b1220c7618f6 (2026-10-12 10:11:16Z).
Historical and current CNKI URL v-tokens differ: match scoped title, journal
and source publication date; never copy historical URL IDs or infer dates.
"""

from __future__ import annotations

from pathlib import Path

from common import DATA_DIR, read_json, write_json

ORIGINAL_OBSERVATION = "2026-10-12T10:11:16+00:00"
RECOVERED_SLJY = "2026-10-12T16:10:59+00:00"
RECOVERED_RURAL = "2026-10-12T16:11:00+00:00"
SLJY = "数量经济技术经济研究"
RURAL = "中国农村经济"

# Exact titles independently proven in the historical seen blob (not inferred
# from the publication date or from the recovery's later detected_at).
EVIDENCE = {
    (SLJY, "常住地基本公共服务与流动人口家庭消费", "2026-10-12"): RECOVERED_SLJY,
    (SLJY, "城市通勤效率提升的要素收入分配效应——基于劳动力空间配置的视角", "2026-10-12"): RECOVERED_SLJY,
    (SLJY, "政府大部制改革的经济促进效应——来自文化和旅游部设立的证据", "2026-10-12"): RECOVERED_SLJY,
    (SLJY, "外部供应风险、技术结构与自主创新能力提升", "2026-10-12"): RECOVERED_SLJY,
    (SLJY, "土地价格政策的资源配置效应", "2026-10-12"): RECOVERED_SLJY,
    (SLJY, "国家大学科技园如何促进高校科技成果转化？——基于创新空间的视角", "2026-10-12"): RECOVERED_SLJY,
    (SLJY, "中国增值税税收归宿测算：基于多区域CGE模型的分析", "2026-10-12"): RECOVERED_SLJY,
    (RURAL, "平台经济发展助推实数深度融合的机制与效应研究", "2026-09-20"): RECOVERED_RURAL,
}


def reconcile(*, data_dir: Path = DATA_DIR) -> dict[str, int]:
    seen_path = data_dir / "seen.json"
    daily_paths = {
        "2026-10-12": data_dir / "daily" / "2026-10-12.json",
        "2026-09-20": data_dir / "daily" / "2026-09-20.json",
    }
    seen = read_json(seen_path, {"papers": {}})
    if not isinstance(seen, dict) or not isinstance(seen.get("papers"), dict):
        raise ValueError("unexpected seen catalogue structure")
    daily = {date: read_json(path, []) for date, path in daily_paths.items()}
    if any(not isinstance(records, list) for records in daily.values()):
        raise ValueError("unexpected Daily bucket structure")

    changed_seen = changed_daily = 0
    # Validate all eight identities and *both* timestamp fields before
    # mutating anything; missing, ambiguous or unexpectedly changed evidence
    # must fail closed rather than silently rewrite a different paper.
    edits: list[tuple[dict, str, str]] = []
    for (journal, title, online_date), recovered_at in EVIDENCE.items():
        def matches(record: dict) -> bool:
            return (
                isinstance(record, dict)
                and record.get("journal") == journal
                and record.get("title") == title
                and record.get("available_online") == online_date
                and record.get("source") == "cnki-rss"
            )

        seen_matches = [p for p in seen["papers"].values() if matches(p)]
        daily_matches = [p for p in daily[online_date] if matches(p)]
        if len(seen_matches) != 1 or len(daily_matches) != 1:
            raise ValueError(f"ambiguous or missing #371 identity: {journal} / {title}")
        seen_paper, daily_paper = seen_matches[0], daily_matches[0]
        if seen_paper.get("id") != daily_paper.get("id"):
            raise ValueError(f"mismatched #371 seen/Daily identity: {title}")
        for paper in (seen_paper, daily_paper):
            if paper.get("first_seen") not in {recovered_at, ORIGINAL_OBSERVATION}:
                raise ValueError(f"unexpected first_seen for {title}")
            if paper.get("detected_at") is not None and paper["detected_at"] not in {
                recovered_at, ORIGINAL_OBSERVATION
            }:
                raise ValueError(f"unexpected detected_at for {title}")
        edits.extend(((seen_paper, recovered_at, "seen"), (daily_paper, recovered_at, "daily")))

    for paper, _, destination in edits:
        if paper["first_seen"] != ORIGINAL_OBSERVATION:
            paper["first_seen"] = ORIGINAL_OBSERVATION
            if destination == "seen":
                changed_seen += 1
            else:
                changed_daily += 1
        # Only fix detected_at when it was already present. Do not synthesize
        # missing metadata or modify source publication/issue dates.
        if "detected_at" in paper and paper["detected_at"] != ORIGINAL_OBSERVATION:
            paper["detected_at"] = ORIGINAL_OBSERVATION

    if changed_seen or any(
        p.get("detected_at") == ORIGINAL_OBSERVATION for p, _, dest in edits if dest == "seen"
    ):
        # Compare with on-disk data: writing a byte-identical seen catalogue
        # on every run causes needless 33 MB commits.
        if seen != read_json(seen_path, {}):
            write_json(seen_path, seen)
    for date, path in daily_paths.items():
        if daily[date] != read_json(path, []):
            write_json(path, daily[date])
    return {"seen_repaired": changed_seen, "daily_repaired": changed_daily}


if __name__ == "__main__":
    result = reconcile()
    print(" ".join(f"{key}={value}" for key, value in result.items()))
