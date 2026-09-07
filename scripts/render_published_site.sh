#!/usr/bin/env bash
set -euo pipefail

DAILY_DATE="$(python - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
print(datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d"))
PY
)"

python - "$DAILY_DATE" <<'PY'
import json
import sys
from pathlib import Path

path = Path("data/daily") / f"{sys.argv[1]}.json"
if not path.exists():
    raise SystemExit(f"canonical daily file is missing: {path}")
payload = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
    raise SystemExit(f"canonical daily file is not a list of records: {path}")
print(f"Canonical input validated: {path} ({len(payload)} records)")
PY

rm -rf docs/paper-index
python scripts/render_site.py
python scripts/build_daily_vnext.py \
  --date "$DAILY_DATE" \
  --output docs/index.html \
  --report "$RUNNER_TEMP/daily-home-report.json"
python scripts/build_daily_vnext.py \
  --date "$DAILY_DATE" \
  --output docs/daily-vnext/index.html \
  --report "$RUNNER_TEMP/daily-vnext-report.json"
python scripts/build_feed.py --site-url https://academic-door.github.io/econ-paper-monitor/
python scripts/render_semantic_scholar_usage.py

test -f docs/index.html
test -f docs/daily-vnext/index.html
test -f docs/feed.xml
test -f docs/usage/index.html
test -f docs/classic/index.html
test -f docs/paper.html
test ! -e docs/daily-vnext/template.html
test ! -e docs/quality
test ! -e docs/admin

python - <<'PY'
from pathlib import Path

root = Path("docs/index.html").read_text(encoding="utf-8")
vnext = Path("docs/daily-vnext/index.html").read_text(encoding="utf-8")
classic = Path("docs/classic/index.html").read_text(encoding="utf-8")
detail = Path("docs/paper.html").read_text(encoding="utf-8")
assert "今日研究时间流" in root and "今日研究时间流" in vnext
assert 'class="sidebar"' not in root and 'class="sidebar"' not in vnext
assert 'class="sidebar"' in classic
assert "title_primary" in detail and "title_secondary" in detail
print("Homepage, Daily vNext, detail, and Classic output contract passed")
PY

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add docs
if git diff --cached --quiet; then
  echo "No generated site changes to commit."
  exit 0
fi

git commit -m "Render paper monitor site"
for attempt in 1 2 3; do
  if git pull --rebase -X theirs origin main && git push origin HEAD:main; then
    exit 0
  fi
  git rebase --abort || true
  echo "Render publish attempt ${attempt} failed; retrying after a short wait."
  sleep $((attempt * 8))
done

echo "Failed to publish generated site after retries."
exit 1
