from pathlib import Path


path = Path("scripts/enrich_metadata.py")
text = path.read_text(encoding="utf-8")

function_anchor = "\n\ndef enrich_abstract_record(record: dict[str, Any], timeout: int) -> tuple[bool, str]:\n"
if function_anchor not in text:
    raise SystemExit("enrich_abstract_record anchor not found")

adapter = '''


def ajcass_content_metadata(record: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Recover 中国农村经济 abstract metadata from its official article API."""
    if str(record.get("journal") or "") != "中国农村经济":
        return {}
    record_url = str(record.get("url") or record.get("source_url") or "")
    match = re.search(r"(?:[?&#])contentId=(\\d+)", record_url)
    if not match:
        return {}
    request_payload = {
        "channelId": 0,
        "contentId": int(match.group(1)),
        "dataShowType": 1,
        "dataSourceType": 3,
        "issue": 0,
        "year": 0,
        "JournalID": 201606270007,
    }
    request = urllib.request.Request(
        "https://api.ajcass.com/api/SiteWebApi/GetContentInfo",
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "User-Agent": "econ-paper-monitor/1.0 (https://github.com/academic-door/econ-paper-monitor)",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = json.loads(response.read().decode(charset))
    except Exception:  # noqa: BLE001 - optional metadata recovery must stay best-effort
        return {}
    data = payload.get("data") if isinstance(payload, dict) else None
    info = data.get("issueContentInfoResult") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return {}
    official_title = clean_text(str(info.get("title") or ""))
    record_title = clean_text(str(record.get("title") or ""))
    if not official_title or not record_title or official_title != record_title:
        return {}
    abstract = clean_abstract_text(str(info.get("abstract") or ""))
    if len(abstract) < 40:
        return {}
    return {"abstract": abstract, "abstract_source": "ajcass_official_api"}
'''
text = text.replace(function_anchor, adapter + function_anchor, 1)

route_anchor = '    changed = False\n    if source_id in {"cepr-dp", "fed-feds"}:\n'
if route_anchor not in text:
    raise SystemExit("abstract retry route anchor not found")
route = '''    changed = False
    if str(record.get("journal") or "") == "中国农村经济" and not str(record.get("abstract") or "").strip():
        metadata = ajcass_content_metadata(record, timeout)
        if metadata.get("abstract"):
            changed = merge_metadata(record, metadata) or changed
            return changed, "abstract-updated:ajcass-official-api"
    if source_id in {"cepr-dp", "fed-feds"}:
'''
text = text.replace(route_anchor, route, 1)
path.write_text(text, encoding="utf-8")
