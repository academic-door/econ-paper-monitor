"""G22 N2 isolated, read-only publisher abstract transport probe. DO NOT MERGE.

One standard-TLS unauthenticated GET per public URL (8s timeout, <=1MiB body).
Never logs publisher text/abstract/HTML, credentials or cookies.
Only reports bounded status, matching title and parseable full abstract presence.
"""
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enrich_metadata import extract_page_metadata, clean_text

PROBES = [
    ("SPRINGER_TAX_A", "https://link.springer.com/article/10.1007/s10797-026-10011-7", "Optimal taxation of robotic capital with a binding minimum wage"),
    ("SPRINGER_TAX_B", "https://link.springer.com/article/10.1007/s10797-026-10004-6", "Local public goods as a double-edged sword"),
    ("SPRINGER_TAX_C", "https://link.springer.com/article/10.1007/s10797-026-10009-1", "Framed norms: the effect of choice and belief information on tax compliance"),
    ("SPRINGER_TAX_D", "https://link.springer.com/article/10.1007/s10797-026-10008-2", "A poorly targeted subsidy? The fiscal and social costs of EU VAT expenditures"),
    ("SPRINGER_TAX_E", "https://link.springer.com/article/10.1007/s10797-026-09999-9", "Subsidy salience and the effectiveness of green policies"),
    ("SPRINGER_TAX_F", "https://link.springer.com/article/10.1007/s10797-026-10010-8", "Audit independence and year-end government spending surges: evidence from China"),
]

def run_one(name, url, title):
    req=urllib.request.Request(url,headers={
        "User-Agent":"Academic-Door-Daily-Door-Source-Quality-Audit/1.0",
        "Accept":"text/html",
    })
    try:
        with urllib.request.urlopen(req,timeout=8,context=ssl.create_default_context() if url.startswith("https") else None) as response:
            status=response.status
            body=response.read(1048577)
            mime=response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        return {"name":name,"result":"HTTP_ERROR","status":exc.code}
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        return {"name":name,"result":"TRANSPORT_ERROR","class":type(exc.reason).__name__ if isinstance(exc,urllib.error.URLError) else type(exc).__name__}
    if len(body)>1048576:
        return {"name":name,"status":status,"mime":mime,"result":"TRUNCATED"}
    html=body.decode("utf-8",errors="replace")
    meta=extract_page_metadata(html)
    plain=clean_text(html)
    abstract=meta.get("abstract") or ""
    return {
        "name":name,"status":status,"mime":mime,"result":"HTTP_RESPONSE",
        "title_present":title.casefold() in plain.casefold(),
        "abstract_extracted":len(abstract)>80,
        "abstract_chars":len(abstract),
        "abstract_source":meta.get("abstract_source"),
        "has_citation_abstract":"citation_abstract" in html.casefold(),
        "has_abstract_heading":"abstract" in plain.casefold(),
    }

def main():
    for args in PROBES:
        print("G22-N2-SAFE "+json.dumps(run_one(*args),ensure_ascii=False,sort_keys=True),flush=True)
    print("G22-N2-SAFE COMPLETE no_records_written=true no_publisher_content_logged=true",flush=True)

if __name__=="__main__":
    main()
