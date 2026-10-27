"""Ephemeral read-only IZA structural diagnosis. Do not merge or schedule.

A maximum of one first-party detail GET per target. Emits only sanitized
HTML element names and CSS class tokens, never publisher text/URLs/secrets.
Network failure is recorded as evidence, not as an intentionally failed CI.
"""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import fetch_text  # noqa: E402

TARGETS = (
    ("18991", "https://www.iza.org/publications/dp/18991/ai-adoption-by-firms-insights-from-the-literature-and-the-german-innovation-survey", "This paper provides an overview"),
    ("18992", "https://www.iza.org/publications/dp/18992/job-seekers-beliefs-and-preferences-over-wages-and-benefits-experimental-evidence-from-online-job-boards", "We estimate how strongly job seekers"),
)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def label(tag: str, attrs: list[tuple[str, str | None]]) -> str:
    raw = next((v for k, v in attrs if k == "class" and v), "")
    # CSS class names only. No raw attribute values, IDs, text or network data.
    classes = [re.sub(r"[^A-Za-z0-9_:\\[\\]#.-]", "", x)[:55] for x in raw.split()[:4]]
    classes = [x for x in classes if x]
    return tag + (("." + ".".join(classes)) if classes else "")


class Structure(HTMLParser):
    def __init__(self, needle: str):
        super().__init__(convert_charrefs=True)
        self.needle = needle.casefold()
        self.stack: list[tuple[str, str]] = []
        self.paths: list[str] = []
        self.paragraphs = 0
        self.in_body = False

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        if tag == "p" and self.in_body:
            self.paragraphs += 1
        if tag not in VOID:
            self.stack.append((tag, label(tag, attrs)))

    def handle_startendtag(self, tag, attrs):
        return None

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag == "body":
            self.in_body = False

    def handle_data(self, data):
        if not self.in_body or not any(tag == "p" for tag, _ in self.stack):
            return
        if self.needle in " ".join(data.split()).casefold():
            path = " > ".join(v for _, v in self.stack[-12:])
            if path not in self.paths:
                self.paths.append(path)


def main():
    for dp, url, needle in TARGETS:
        try:
            html = fetch_text(url, timeout=12)
        except Exception as exc:
            code = getattr(exc, "code", None)
            code = code if isinstance(code, int) else "none"
            print(f"IZA-DOM dp={dp} state=transport_error type={type(exc).__name__} http={code}")
            continue
        parser = Structure(needle)
        parser.feed(html)
        print(f"IZA-DOM dp={dp} state=transport_ok bytes={len(html)} body_p_count={parser.paragraphs} target_paths={len(parser.paths)}")
        for n, path in enumerate(parser.paths[:3]):
            print(f"IZA-DOM dp={dp} target_path_{n}={path}")
        if not parser.paths:
            print(f"IZA-DOM dp={dp} state=body_text_path_missing")


if __name__ == "__main__":
    main()
