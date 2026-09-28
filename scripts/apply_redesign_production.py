#!/usr/bin/env python3
"""Apply the Human-approved Daily Door redesign to generated production HTML.

The accepted visual/interaction transform is shared with the isolated #329
preview, but production keeps its own canonical URLs, robots policy, analytics,
and detail routes.  This step changes presentation only; canonical data and
first-discovery semantics remain untouched.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from build_redesign_preview import PREVIEW_SCRIPT, PREVIEW_STYLE, strip_presence_script

STYLE_ID = "daily-door-redesign-preview-style"
SCRIPT_ID = "daily-door-redesign-preview-script"


def _remove_existing(document: str) -> str:
    document = re.sub(
        rf'<style\s+id=["\']{re.escape(STYLE_ID)}["\'][^>]*>.*?</style>\s*',
        "",
        document,
        flags=re.I | re.S,
    )
    document = re.sub(
        rf'<script\s+id=["\']{re.escape(SCRIPT_ID)}["\'][^>]*>.*?</script>\s*',
        "",
        document,
        flags=re.I | re.S,
    )
    return document


def process_html(path: Path) -> None:
    document = path.read_text(encoding="utf-8")
    document = _remove_existing(document)
    # The approved redesign removes public presence chrome.  Remove its client
    # too so production does not make an otherwise invisible heartbeat request.
    document = strip_presence_script(document)

    if PREVIEW_STYLE not in document:
        document = document.replace("</head>", PREVIEW_STYLE + "\n</head>", 1)
    if PREVIEW_SCRIPT not in document:
        document = document.replace("</body>", PREVIEW_SCRIPT + "\n</body>", 1)
    path.write_text(document, encoding="utf-8")


def target_html(root: Path) -> list[Path]:
    paths: set[Path] = set()
    for relative in (
        "index.html",
        "recent72/index.html",
        "topics/china/index.html",
        "search/index.html",
    ):
        path = root / relative
        if path.exists():
            paths.add(path)

    for relative in ("working-papers", "journals"):
        folder = root / relative
        if folder.exists():
            paths.update(folder.rglob("*.html"))

    return sorted(paths)


def apply(root: Path) -> int:
    paths = target_html(root)
    for path in paths:
        process_html(path)
    print(f"Applied approved Daily Door redesign to {len(paths)} production HTML files")
    return len(paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("docs"))
    args = parser.parse_args()
    count = apply(args.root.resolve())
    if count < 6:
        raise SystemExit(f"expected at least 6 production surfaces, found {count}")


if __name__ == "__main__":
    main()
