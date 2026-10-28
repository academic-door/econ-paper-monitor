"""Ephemeral #404 JARE recognized-syndication transport audit; read-only, never merge.

One bounded unauthenticated HTTPS GET per public endpoint. No source bypass,
cookies, proxy, retry, production dispatch, or contents mutation.
Only sanitized status/structure appears in logs; timestamps are not promoted
to publisher first-online dates.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from xml.etree import ElementTree as ET

PROBES = {
    "AGECON_OAI_IDENTIFY": "https://ageconsearch.umn.edu/oai2d?verb=Identify",
    "AGECON_OAI_FORMATS": "https://ageconsearch.umn.edu/oai2d?verb=ListMetadataFormats",
    "AGECON_OAI_TARGET": "https://ageconsearch.umn.edu/oai2d?verb=GetRecord&metadataPrefix=marcxml&identifier=oai%3Aageconsearch.umn.edu%3A376286",
    "AGECON_ACCEPTED_RECORD": "https://ageconsearch.umn.edu/record/393803",
    "AGECON_DISCREPANT_RECORD": "https://ageconsearch.umn.edu/record/376286",
    "REPEC_DISCREPANT_RECORD": "https://ideas.repec.org/a/ags/jlaare/376286.html",
}

def describe(label: str, url: str) -> dict[str, object]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Academic-Door-Daily-Door-Source-Audit/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=9, context=ssl.create_default_context()) as response:
            body = response.read(131073)
            code = response.status
            mime = response.headers.get_content_type()
            truncated = len(body) > 131072
    except urllib.error.HTTPError as exc:
        body, code, mime, truncated = b"", exc.code, "HTTP_ERROR", False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        name = type(exc.reason).__name__ if isinstance(exc, urllib.error.URLError) else type(exc).__name__
        return {"name": label, "result": "TRANSPORT_ERROR", "error_class": name}

    data = {
        "name": label, "result": "HTTP_RESPONSE", "status": code,
        "mime": mime, "bytes_limited": len(body), "truncated": truncated,
    }
    text = body.decode("utf-8", errors="replace").casefold()
    data["has_target_doi"] = "10.22004/ag.econ.376286" in text
    data["has_target_title"] = "dynamics of us farm income distribution under weather shocks" in text
    if body.lstrip().startswith(b"<?xml") or body.lstrip().startswith(b"<OAI"):
        try:
            xml = ET.fromstring(body)
            data["xml_local_root"] = xml.tag.split("}")[-1]
            data["oai_records"] = len(xml.findall(".//{*}record"))
            data["oai_errors"] = len(xml.findall(".//{*}error"))
            data["oai_datestamps"] = len(xml.findall(".//{*}datestamp"))
        except ET.ParseError:
            data["xml_parse"] = "INVALID_OR_TRUNCATED"
    return data

def inspect_real_oai_identifier() -> None:
    """One page of identifiers, optionally one GetRecord with a REAL returned ID."""
    import json
    from urllib.parse import quote
    endpoint = "https://ageconsearch.umn.edu/oai2d"
    url = endpoint + "?verb=ListIdentifiers&metadataPrefix=marcxml&from=2026-09-01"
    req = urllib.request.Request(url, headers={"User-Agent": "Academic-Door-Daily-Door-Source-Audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=9, context=ssl.create_default_context()) as response:
            body = response.read(131073)
            code = response.status
    except urllib.error.HTTPError as exc:
        print("JARE-AGECON-SAFE " + json.dumps(
            {"name": "AGECON_OAI_IDENTIFIERS", "status": exc.code, "result": "HTTP_ERROR"}, sort_keys=True
        ), flush=True)
        return
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print("JARE-AGECON-SAFE " + json.dumps(
            {"name": "AGECON_OAI_IDENTIFIERS", "result": "TRANSPORT_ERROR", "class": type(exc).__name__}, sort_keys=True
        ), flush=True)
        return

    if len(body) > 131072:
        print("JARE-AGECON-SAFE " + json.dumps(
            {"name": "AGECON_OAI_IDENTIFIERS", "status": code, "result": "TRUNCATED_PAGE"}, sort_keys=True
        ), flush=True)
        return
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        print("JARE-AGECON-SAFE " + json.dumps(
            {"name": "AGECON_OAI_IDENTIFIERS", "status": code, "result": "NON_XML"}, sort_keys=True
        ), flush=True)
        return

    headers = root.findall(".//{*}header")
    dates = sorted(
        d.text or "" for d in root.findall(".//{*}header/{*}datestamp") if d.text
    )
    ids = [i.text for i in root.findall(".//{*}header/{*}identifier") if i.text]
    print("JARE-AGECON-SAFE " + json.dumps({
        "name": "AGECON_OAI_IDENTIFIERS", "status": code,
        "xml_root": root.tag.split("}")[-1], "headers_in_first_page": len(headers),
        "first_id_present": bool(ids), "resumption_token_present": any(
            x.text for x in root.findall(".//{*}resumptionToken")
        ),
        "metadata_modified_min": dates[0] if dates else None,
        "metadata_modified_max": dates[-1] if dates else None,
        "oai_errors": len(root.findall(".//{*}error")),
        "note": "datestamp is index modification, NOT publisher first-online date",
    }, sort_keys=True), flush=True)
    if ids:
        get_url = endpoint + "?verb=GetRecord&metadataPrefix=marcxml&identifier=" + quote(ids[0], safe="")
        print("JARE-AGECON-SAFE " + json.dumps(
            describe("AGECON_OAI_REAL_ID_GETRECORD", get_url), sort_keys=True
        ), flush=True)


def main() -> None:
    import json
    for label, url in PROBES.items():
        print("JARE-AGECON-SAFE " + json.dumps(describe(label, url), sort_keys=True), flush=True)
    inspect_real_oai_identifier()
    print("JARE-AGECON-SAFE AUDIT_COMPLETE no_source_mutation=true no_publisher_acceptance_inferred=true")

if __name__ == "__main__":
    main()
