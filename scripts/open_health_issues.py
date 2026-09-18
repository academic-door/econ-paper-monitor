"""Open or update GitHub issues when monitored health crosses thresholds.

Reads the operational health artifacts written by the data workflow and keeps
one open issue per anomaly slug.  Idempotency is enforced by a stable title
prefix: an existing open issue with the same slug is updated instead of
recreated, and it is closed automatically once the anomaly recovers.

Rules:

* ``data/release_gate.json`` ``ok != true`` -> release-gate issue
* ``data/source_health.json`` degraded >= threshold or unavailable > 0
  -> degraded-sources / source-unavailable issue
* ``data/local_cnki_status.json`` ``last_success_at`` older than the max age
  -> local-cnki-stale issue
* latest legitimate Semantic Scholar provider telemetry reports key unconfigured
  -> semantic-scholar-key issue
* latest Semantic Scholar provider telemetry crosses the throttle ratio
  -> semantic-scholar-throttled issue

The script never prints credentials.  GitHub access uses ``GITHUB_TOKEN``.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DATA_DIR, now_iso, read_json


def _age_hours(value: Any, now: datetime) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (now - stamp.astimezone(timezone.utc)).total_seconds() / 3600)


def build_anomalies(
    data_dir: Path = DATA_DIR,
    *,
    degraded_threshold: int = 25,
    cnki_max_age_hours: float = 30.0,
    elsevier_weekly_warning: int = 16000,  # 80% of the 20,000/week Elsevier quota
    quota_remaining_ratio: float = 0.2,
    ss_throttle_ratio: float = 0.3,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Return anomaly descriptors with stable slugs and issue bodies."""
    now = now or datetime.now(timezone.utc)
    anomalies: list[dict[str, str]] = []

    gate = read_json(data_dir / "release_gate.json", {})
    if not isinstance(gate, dict) or gate.get("ok") is not True:
        failures = gate.get("failures") if isinstance(gate, dict) else []
        body = ["Release gate is not passing.  Failures:", ""]
        if isinstance(failures, list):
            for item in failures[:20]:
                body.append(f"- {item.get('code')} count={item.get('count')}")
        else:
            body.append(f"- {failures}")
        body.append("")
        body.append(f"checked_at={now_iso()}")
        anomalies.append(
            {
                "slug": "release-gate",
                "title": "Release gate failed",
                "body": "\n".join(body),
            }
        )

    source_health = read_json(data_dir / "source_health.json", {})
    counts = source_health.get("counts") if isinstance(source_health, dict) else {}
    degraded = int(counts.get("degraded") or 0)
    unavailable = int(counts.get("unavailable") or 0)
    if degraded >= max(1, degraded_threshold):
        degraded_rows = source_health.get("degraded") if isinstance(source_health, dict) else []
        names = [
            str(row.get("journal") or row.get("journal_id") or "?")
            for row in (degraded_rows if isinstance(degraded_rows, list) else [])
        ][:20]
        anomalies.append(
            {
                "slug": "degraded-sources",
                "title": f"Degraded sources ({degraded})",
                "body": (
                    f"Degraded formal sources: {degraded} (threshold {degraded_threshold}).\n\n"
                    + "\n".join(f"- {name}" for name in names)
                    + f"\n\nchecked_at={now_iso()}"
                ),
            }
        )
    if unavailable > 0:
        unavailable_rows = source_health.get("unavailable") if isinstance(source_health, dict) else []
        names = [
            str(row.get("journal") or row.get("journal_id") or "?")
            for row in (unavailable_rows if isinstance(unavailable_rows, list) else [])
        ][:20]
        anomalies.append(
            {
                "slug": "source-unavailable",
                "title": f"Formal sources unavailable ({unavailable})",
                "body": (
                    f"Unavailable formal sources: {unavailable}.\n\n"
                    + "\n".join(f"- {name}" for name in names)
                    + f"\n\nchecked_at={now_iso()}"
                ),
            }
        )

    local_cnki = read_json(data_dir / "local_cnki_status.json", {})
    last_success = local_cnki.get("last_success_at") if isinstance(local_cnki, dict) else None
    age_hours = _age_hours(last_success, now)
    if age_hours is None or age_hours > cnki_max_age_hours:
        age_text = f"{age_hours:.1f}" if age_hours is not None else "unknown"
        anomalies.append(
            {
                "slug": "local-cnki-stale",
                "title": "Local CNKI update stale",
                "body": (
                    f"Local CNKI last_success_at={last_success or 'missing'} "
                    f"age_hours={age_text} (max {cnki_max_age_hours}).\n\n"
                    f"checked_at={now_iso()}"
                ),
            }
        )

    provider_usage = read_json(data_dir / "semantic_scholar_usage.json", {})
    providers_usage = provider_usage.get("providers") if isinstance(provider_usage, dict) else {}
    health_latest = read_json(data_dir / "metadata_provider_health.json", {}).get("latest") or {}
    health_providers = health_latest.get("providers") if isinstance(health_latest, dict) else {}

    ss_latest = health_providers.get("semantic-scholar") if isinstance(health_providers, dict) else {}
    if isinstance(ss_latest, dict) and ss_latest and ss_latest.get("api_key_configured") is False:
        anomalies.append(
            {
                "slug": "semantic-scholar-key",
                "title": "Semantic Scholar key not configured",
                "body": (
                    "Latest legitimate provider telemetry reports "
                    "api_key_configured=false for SEMANTIC_SCHOLAR_API_KEY. "
                    "Metadata recovery can fail open to accepted fallback providers; "
                    "verify the canonical secret configuration without sending synthetic traffic.\n\n"
                    f"checked_at={now_iso()}"
                ),
            }
        )

    els_usage = providers_usage.get("elsevier") if isinstance(providers_usage, dict) else {}
    els_triggers: list[str] = []
    if isinstance(els_usage, dict) and els_usage:
        headers = els_usage.get("rate_limit_headers")
        if isinstance(headers, dict) and headers:
            try:
                limit = int(headers.get("X-RateLimit-Limit") or 0)
                remaining = int(headers.get("X-RateLimit-Remaining") or 0)
            except (TypeError, ValueError):
                limit = remaining = 0
            if limit > 0 and remaining < max(1, int(limit * quota_remaining_ratio)):
                els_triggers.append(f"X-RateLimit-Remaining low: {remaining}/{limit}")
        weekly = int(els_usage.get("weekly_requests_7d") or 0)
        if weekly >= elsevier_weekly_warning:
            els_triggers.append(
                f"weekly_requests_7d={weekly} >= warning threshold {elsevier_weekly_warning}"
            )
    els_latest = health_providers.get("elsevier") if isinstance(health_providers, dict) else {}
    if isinstance(els_latest, dict) and int(els_latest.get("rate_limited") or 0) > 0:
        els_triggers.append(f"latest run rate_limited={els_latest.get('rate_limited')}")
    if els_triggers:
        anomalies.append(
            {
                "slug": "elsevier-quota",
                "title": "Elsevier API quota warning",
                "body": (
                    "Elsevier API key usage is approaching or hitting limits:\n\n"
                    + "\n".join(f"- {item}" for item in els_triggers)
                    + "\n\nQuota resets every 7 days; check the usage page "
                    "(docs/usage/index.html) and the key settings on the "
                    "Elsevier Developer Portal.\n\n"
                    f"checked_at={now_iso()}"
                ),
            }
        )

    if isinstance(ss_latest, dict):
        ss_attempts = int(ss_latest.get("attempts") or 0)
        ss_rate = int(ss_latest.get("rate_limited") or 0)
        if ss_attempts > 0 and (ss_rate / ss_attempts) >= ss_throttle_ratio:
            anomalies.append(
                {
                    "slug": "semantic-scholar-throttled",
                    "title": "Semantic Scholar rate-limited",
                    "body": (
                        f"Latest run rate_limited={ss_rate}/{ss_attempts} "
                        f"(ratio {ss_rate / ss_attempts:.1%} >= {ss_throttle_ratio:.0%}). "
                        "The provider keyed ceiling is 1 RPS; the accepted client target remains 0.2 RPS / 5s. Check the pacing and the "
                        "usage page.\n\n"
                        f"checked_at={now_iso()}"
                    ),
                }
            )

    return anomalies


def _request(method: str, url: str, token: str, payload: Any = None) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "econ-paper-monitor-health",
    }
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = {"message": raw[:200]}
        raise RuntimeError(f"github api {exc.code}: {detail.get('message') or detail}") from exc


def list_open_issues(repo: str, token: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    page = 1
    while page <= 10:
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=open&per_page=100&page={page}"
        )
        batch = _request("GET", url, token)
        if not isinstance(batch, list):
            break
        issues.extend(item for item in batch if "pull_request" not in item)
        if len(batch) < 100:
            break
        page += 1
    return issues


def sync_issues(
    anomalies: list[dict[str, str]],
    *,
    repo: str,
    issue_prefix: str,
    token: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create/update/close issues idempotently and report the actions taken."""
    open_issues = list_open_issues(repo, token) if not dry_run else []
    by_slug: dict[str, dict[str, Any]] = {}
    for issue in open_issues:
        title = str(issue.get("title") or "")
        if title.startswith(issue_prefix):
            slug = title[len(issue_prefix):].strip().split(" ", 1)[0].rstrip(":")
            by_slug[slug] = issue

    created: list[str] = []
    updated: list[str] = []
    closed: list[str] = []
    for anomaly in anomalies:
        slug = anomaly["slug"]
        title = f"{issue_prefix} {slug}: {anomaly['title']}"
        body = anomaly["body"]
        existing = by_slug.get(slug)
        if existing:
            if dry_run:
                updated.append(slug)
                continue
            _request(
                "PATCH",
                f"https://api.github.com/repos/{repo}/issues/{existing['number']}",
                token,
                {"title": title, "body": body},
            )
            updated.append(slug)
        else:
            if dry_run:
                created.append(slug)
                continue
            _request(
                "POST",
                f"https://api.github.com/repos/{repo}/issues",
                token,
                {"title": title, "body": body},
            )
            created.append(slug)

    active_slugs = {anomaly["slug"] for anomaly in anomalies}
    for slug, issue in by_slug.items():
        if slug in active_slugs:
            continue
        if dry_run:
            closed.append(slug)
            continue
        _request(
            "PATCH",
            f"https://api.github.com/repos/{repo}/issues/{issue['number']}",
            token,
            {"state": "closed"},
        )
        closed.append(slug)

    return {
        "repo": repo,
        "dry_run": dry_run,
        "created": created,
        "updated": updated,
        "closed": closed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--issue-prefix", default="[Monitor Health]")
    parser.add_argument("--degraded-threshold", type=int, default=25)
    parser.add_argument("--cnki-max-age-hours", type=float, default=30.0)
    parser.add_argument("--elsevier-weekly-warning", type=int, default=16000)
    parser.add_argument("--quota-remaining-ratio", type=float, default=0.2)
    parser.add_argument("--ss-throttle-ratio", type=float, default=0.3)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    anomalies = build_anomalies(
        args.data_dir,
        degraded_threshold=args.degraded_threshold,
        cnki_max_age_hours=args.cnki_max_age_hours,
        elsevier_weekly_warning=args.elsevier_weekly_warning,
        quota_remaining_ratio=args.quota_remaining_ratio,
        ss_throttle_ratio=args.ss_throttle_ratio,
    )
    if not args.repo:
        raise SystemExit("missing repo: pass --repo or set GITHUB_REPOSITORY")
    token = os.environ.get(args.token_env, "")
    if not token and not args.dry_run:
        raise SystemExit(f"missing token env {args.token_env}")
    report = sync_issues(
        anomalies,
        repo=args.repo,
        issue_prefix=args.issue_prefix,
        token=token,
        dry_run=args.dry_run,
    )
    print(
        f"health issues repo={report['repo']} dry_run={report['dry_run']} "
        f"created={len(report['created'])} updated={len(report['updated'])} "
        f"closed={len(report['closed'])} active={len(anomalies)}"
    )
    for slug in report["created"]:
        print(f"CREATED {slug}")
    for slug in report["updated"]:
        print(f"UPDATED {slug}")
    for slug in report["closed"]:
        print(f"CLOSED {slug}")


if __name__ == "__main__":
    main()