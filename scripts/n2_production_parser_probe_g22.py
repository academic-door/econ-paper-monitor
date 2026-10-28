"""G22 N2: isolate which strict production publisher check rejected a sample.

One ordinary HTTPS GET, no retries, cookies, bypass, or data mutations.
No article content or title is printed; only booleans and lengths.
This file and workflow are diagnostic ONLY -- do not merge.
"""
from __future__ import annotations
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from enrich_metadata import clean_text, clean_abstract_text, parse_meta

ROOT=Path(__file__).resolve().parents[1]
DOI="10.1007/s10797-026-10011-7"

def main():
    data=json.loads((ROOT/"data"/"daily"/"2026-10-27.json").read_text(encoding="utf-8"))
    record=next(x for x in data if x.get("doi")==DOI)
    title=clean_text(record["title"]).casefold()
    url=f"https://link.springer.com/article/{DOI}"
    request=urllib.request.Request(url,headers={
        "User-Agent":"Academic-Door-Daily-Door-Source-Quality-Audit/1.0",
        "Accept":"text/html",
    })
    try:
        with urllib.request.urlopen(request,timeout=8) as response:
            payload=response.read(1_048_577)
            status=response.status
            mime=response.headers.get_content_type()
            final_host=urllib.parse.urlparse(response.geturl()).hostname
    except urllib.error.HTTPError as exc:
        print("G22_N2_REASON "+json.dumps({"transport":"HTTP_ERROR","status":exc.code}),flush=True);return
    except Exception as exc:
        print("G22_N2_REASON "+json.dumps({"transport":"EXCEPTION","class":type(exc).__name__}),flush=True);return
    html=payload.decode("utf-8",errors="replace")
    meta=parse_meta(html)
    heading=re.search(r"<h1\b[^>]*>([\s\S]*?)</h1>",html,flags=re.I)
    from_h1=clean_text(heading.group(1) if heading else "").casefold()
    from_citation=clean_text(meta.get("citation_title") or "").casefold()
    selected=from_citation or from_h1
    abstract=clean_abstract_text(meta.get("dc.description"))
    print("G22_N2_REASON "+json.dumps({
        "transport":"HTTP_RESPONSE","status":status,
        "mime_html":mime=="text/html",
        "host_valid":final_host=="link.springer.com",
        "body_within_cap":len(payload)<=1_048_576,
        "body_bytes":len(payload),
        "doi_in_html":DOI in html.casefold(),
        "citation_title_present":bool(from_citation),
        "citation_title_exact":from_citation==title,
        "h1_title_exact":from_h1==title,
        "selected_title_exact":selected==title,
        "abstract_len":len(abstract),
        "abstract_acceptable_length":180<=len(abstract)<=12000,
        "abstract_truncated":bool(re.search(r"(?:\.{3,}|…+)\s*$",abstract)),
        "abstract_preview":("this is a preview of subscription content" in abstract.casefold()),
        "read_only":True,
    },sort_keys=True),flush=True)

if __name__=="__main__":
    main()
