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

def main() -> None:
    import json
    for label, url in PROBES.items():
        print("JARE-AGECON-SAFE " + json.dumps(describe(label, url), sort_keys=True), flush=True)
    print("JARE-AGECON-SAFE AUDIT_COMPLETE no_source_mutation=true no_publisher_acceptance_inferred=true")

if __name__ == "__main__":
    main()
