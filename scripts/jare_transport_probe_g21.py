"""Ephemeral single-attempt first-party JARE transport/classification evidence for #404.

This module is deliberately not a production adapter. No retries, proxies, custom
TLS contexts, credentials, stored HTML, article contents or publisher challenge bypass.
Only status buckets, content length and parser record counts enter CI logs.
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fetch_priority_toc import BROWSER_HEADERS, is_challenge_page, jare_advance_blocks, jare_rest_blocks

TARGETS = (
    ("REST", "https://jareonline.org/wp-json/wp/v2/preprints2022?per_page=100&_fields=id,title,acf,link,content"),
    ("HTML", "https://jareonline.org/preprint-online/"),
)


def error_bucket(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return "dns_error"
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "timeout"
        if isinstance(reason, (ssl.SSLError, ssl.SSLCertVerificationError)):
            return "tls_error"
        if isinstance(reason, ConnectionRefusedError):
            return "refused"
        if isinstance(reason, ConnectionResetError):
            return "reset"
        return f"url_error_{type(reason).__name__[:40]}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    return f"error_{type(exc).__name__[:40]}"


def main() -> None:
    for kind, url in TARGETS:
        try:
            # Exactly one secure publisher request per official URL; no fallback
            # credentials, unverified SSL context, changed host or retry.
            request = urllib.request.Request(url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(request, timeout=12) as response:
                status = response.status
                payload = response.read(1_500_001)
                mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if len(payload) > 1_500_000:
                print(f"JARE-SAFE kind={kind} result=oversized status={status}")
                continue
            text = payload.decode("utf-8", errors="replace")
            if is_challenge_page(text):
                print(f"JARE-SAFE kind={kind} result=CHALLENGE status={status} size={len(payload)}")
                continue
            if kind == "REST":
                try:
                    structure = json.loads(text)
                    raw_count = len(structure) if isinstance(structure, list) else -1
                    parsed_count = len(jare_rest_blocks(text))
                    print(f"JARE-SAFE kind=REST result=ok status={status} mime={mime} size={len(payload)} objects={raw_count} parsed={parsed_count}")
                except Exception:
                    print(f"JARE-SAFE kind=REST result=unparseable status={status} mime={mime} size={len(payload)}")
            else:
                parsed_count = len(jare_advance_blocks(text, url))
                print(f"JARE-SAFE kind=HTML result=ok status={status} mime={mime} size={len(payload)} parsed={parsed_count}")
        except Exception as exc:
            print(f"JARE-SAFE kind={kind} result={error_bucket(exc)}")


if __name__ == "__main__":
    main()
