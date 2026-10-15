"""Run the local-only Chinese journal supplement pipeline.

This entrypoint is intended for a Windows scheduled task. It keeps CNKI RSS on
the user's local network, then publishes only normalized site data to GitHub.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path

from common import DATA_DIR, ROOT, read_json, today_str, write_json
from status import now, record_source, record_workflow_run


LOG_DIR = ROOT / "local_admin" / "logs"
LOG_PATH = LOG_DIR / "local-cnki-update.log"
RUNTIME_DIR = ROOT / "local_admin" / "runtime"
LOCAL_STATUS_PATH = DATA_DIR / "local_cnki_status.json"
MAX_LOG_BYTES = 2_000_000
KEEP_LOG_BYTES = 400_000


def configure_console() -> None:
    """Keep scheduled-task logging alive on legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def log_path_for_status() -> str:
    """Return a repository-relative log path for public status metadata."""
    return str(LOG_PATH.relative_to(ROOT)).replace("\\", "/")


def write_local_status(
    state: str,
    *,
    message: str,
    count: int = 0,
    finished_at: str | None = None,
    source_health: dict | None = None,
) -> None:
    """Persist CNKI ownership outside the shared, frequently rewritten ledger."""
    previous = read_json(LOCAL_STATUS_PATH, {})
    payload = {
        "state": state,
        "ok": state in {"success", "published"},
        "count": count,
        "message": message,
        "updated_at": now(),
        "last_success_at": previous.get("last_success_at"),
    }
    if isinstance(source_health, dict):
        for key in ("selected_sources", "successful_sources", "failed_sources"):
            if key in source_health:
                payload[key] = source_health[key]
    if state in {"success", "published"}:
        payload["last_success_at"] = finished_at or payload["updated_at"]
    write_json(LOCAL_STATUS_PATH, payload)


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log(LOG_PATH)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")
    print(message)


def rotate_log(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= MAX_LOG_BYTES:
        return
    payload = path.read_bytes()[-KEEP_LOG_BYTES:]
    path.write_bytes(payload)


def prune_old_files(directory: Path, *, older_than_days: int) -> int:
    if not directory.exists():
        return 0
    cutoff = datetime.now().timestamp() - older_than_days * 24 * 60 * 60
    removed = 0
    for path in directory.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def run_step(
    command: list[str],
    *,
    allow_failure: bool = False,
    extra_env: dict[str, str] | None = None,
    display_command: list[str] | None = None,
) -> int:
    log("$ " + " ".join(display_command or command))
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.stdout:
        for line in completed.stdout.splitlines():
            log("  " + line)
    if completed.returncode and not allow_failure:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed.returncode


def git_has_staged_changes() -> bool:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode != 0


def push_command() -> tuple[list[str], dict[str, str]]:
    """Return a push command and private environment additions.

    Interactive Git Credential Manager remains the default. A scheduled task
    can instead provide GITHUB_PUBLISH_TOKEN without putting the token in the
    remote URL or command-line log.
    """
    token = os.environ.get("GITHUB_PUBLISH_TOKEN", "").strip()
    if not token:
        return ["git", "push"], {}
    encoded = base64.b64encode(f"x-access-token:{token}".encode("ascii")).decode("ascii")
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
    return ["git", "-c", "http.sslbackend=openssl", "push"], {
        "GIT_CONFIG_COUNT": str(count + 1),
        f"GIT_CONFIG_KEY_{count}": "http.https://github.com/.extraheader",
        f"GIT_CONFIG_VALUE_{count}": f"AUTHORIZATION: basic {encoded}",
    }


def push_with_retries(attempts: int = 4) -> None:
    last_code = 0
    for attempt in range(1, attempts + 1):
        command, extra_env = push_command()
        last_code = run_step(
            command,
            allow_failure=True,
            extra_env=extra_env,
            display_command=["git", "push"],
        )
        if last_code == 0:
            return
        wait_seconds = min(120, attempt * 30)
        log(f"git push failed; retrying in {wait_seconds} seconds ({attempt}/{attempts}).")
        import time

        time.sleep(wait_seconds)
    raise RuntimeError(f"git push failed after {attempts} attempts; last exit code={last_code}")


def sync_runner_to_public_main() -> None:
    """Reset the disposable runner to the latest public main.

    The runner contains generated monitoring data only. Resetting before each
    run avoids merging stale generated state when GitHub Actions or a previous
    local run advanced main while this machine was offline. The raw CNKI feed
    is fetched again below, so an unpublished data commit is recoverable.
    """
    fetch_public_main_with_retries()
    run_step(["git", "reset", "--hard", "origin/main"])


def fetch_public_main_with_retries(attempts: int = 4) -> None:
    """Refresh the runner baseline while tolerating transient TLS EOFs."""
    import time

    last_code = 0
    for attempt in range(1, attempts + 1):
        last_code = run_step(
            ["git", "-c", "http.sslbackend=openssl", "fetch", "origin", "main"],
            allow_failure=True,
        )
        if last_code == 0:
            return
        if attempt < attempts:
            wait_seconds = min(60, attempt * 10)
            log(f"git fetch failed; retrying in {wait_seconds} seconds ({attempt}/{attempts}).")
            time.sleep(wait_seconds)
    raise RuntimeError(f"git fetch failed after {attempts} attempts; last exit code={last_code}")


def publish_final_status() -> None:
    """Publish the post-push result instead of leaving ``pending`` public."""
    run_step(["git", "add", "data/status.json", "data/local_cnki_status.json"])
    if not git_has_staged_changes():
        return
    run_step(["git", "commit", "-m", "Record local CNKI publish status"])
    push_with_retries()


def prepare_cnki_raw_input(temp_output: Path) -> tuple[Path, int]:
    input_dir = RUNTIME_DIR / "raw-input"
    input_feed_dir = input_dir / "cnki-rss"
    input_feed_dir.mkdir(parents=True, exist_ok=True)
    for old_file in input_feed_dir.glob("*.json"):
        old_file.unlink()

    records = json.loads(temp_output.read_text(encoding="utf-8-sig")) if temp_output.exists() else []
    target = ROOT / "data" / "raw" / "cnki-rss" / f"{today_str()}.json"
    target_status = target.with_suffix(".status.json")
    if not isinstance(records, list) or not records:
        log("CNKI RSS fetch produced no records; preserving existing raw cache.")
        if target.exists():
            cached = json.loads(target.read_text(encoding="utf-8-sig"))
            if isinstance(cached, list) and cached:
                shutil.copyfile(target, input_feed_dir / target.name)
                log(f"Using preserved CNKI RSS cache with {len(cached)} records for dedupe input.")
                return input_dir, len(cached)
        return input_dir, 0
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(temp_output, target)
    shutil.copyfile(temp_output, input_feed_dir / target.name)
    temp_status = temp_output.with_suffix(".status.json")
    if temp_status.exists():
        shutil.copyfile(temp_status, target_status)
    log(f"Promoted {len(records)} CNKI RSS records to {target}")
    return input_dir, len(records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true", help="Run pipeline without committing/pushing generated updates.")
    parser.add_argument("--max-age-days", type=int, default=90)
    args = parser.parse_args()
    configure_console()

    python = sys.executable
    start = datetime.now().isoformat(timespec="seconds")
    log("=" * 72)
    log(f"Local CNKI update started at {start}")
    removed_runtime = prune_old_files(RUNTIME_DIR, older_than_days=14)
    removed_raw = prune_old_files(ROOT / "data" / "raw" / "cnki-rss", older_than_days=60)
    if removed_runtime or removed_raw:
        log(f"Pruned old local artifacts: runtime={removed_runtime}, cnki_raw={removed_raw}")
    record_source("local-cnki-run", ok=False, count=0, message="running")
    record_source("local-cnki-publish", ok=False, count=0, message="not attempted")
    write_local_status("running", message="本地 CNKI 链路运行中")
    final_status_recorded = False

    try:
        if not args.no_push:
            # A local supplement must start from the current public main. If
            # the GitHub updater is publishing at the same time, the runner
            # starts from the new public main instead of merging generated
            # pages from an older checkout.
            sync_runner_to_public_main()

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        cnki_temp = RUNTIME_DIR / f"cnki-rss-{today_str()}.json"
        run_step(
            [
                python,
                "scripts/fetch_cnki_rss.py",
                "--max-age-days",
                str(args.max_age_days),
                "--require-all-sources",
                "--output",
                str(cnki_temp),
            ]
        )
        cnki_status_path = cnki_temp.with_suffix(".status.json")
        cnki_summaries = read_json(cnki_status_path, [])
        source_health = {
            "selected_sources": len(cnki_summaries) if isinstance(cnki_summaries, list) else 0,
            "successful_sources": sum(
                1 for item in cnki_summaries
                if isinstance(item, dict) and item.get("ok") is True
            ) if isinstance(cnki_summaries, list) else 0,
            "failed_sources": sum(
                1 for item in cnki_summaries
                if not isinstance(item, dict) or item.get("ok") is not True
            ) if isinstance(cnki_summaries, list) else 0,
        }
        if (
            not isinstance(cnki_summaries, list)
            or not cnki_summaries
            or source_health["failed_sources"]
            or source_health["successful_sources"] != source_health["selected_sources"]
        ):
            raise RuntimeError(f"CNKI source status is incomplete: {source_health}")
        cnki_raw_input, cnki_count = prepare_cnki_raw_input(cnki_temp)
        if cnki_count:
            run_step([python, "scripts/merge_local_cnki.py", "--input", str(cnki_temp)])
        else:
            log("No CNKI RSS records available; preserving existing paper catalogue.")
        # The local runner may start without today's bucket when the fetch
        # degraded; release_gate and product_audit must never treat that as a
        # canonical-data failure.
        run_step([python, "scripts/ensure_today_daily.py"])
        run_step(
            [
                python,
                "scripts/enrich_metadata.py",
                "--latest-days",
                "1",
                "--limit",
                "80",
                "--workers",
                "4",
                "--timeout",
                "20",
            ],
            allow_failure=True,
        )
        # Working-paper pages are often discovered from a catalogue whose
        # official date is older than the discovery date. Run the lightweight
        # abstract/author routes for today's bucket as well, then quarantine
        # clearly historical catalogue items before rendering the homepage.
        run_step(
            [
                python,
                "scripts/enrich_metadata.py",
                "--abstract-only",
                "--latest-days",
                "1",
                "--limit",
                "120",
                "--workers",
                "4",
                "--timeout",
                "20",
            ],
            allow_failure=True,
        )
        run_step([python, "scripts/clean_historical_working_papers.py"])
        # Enrichment can contribute publisher dates; normalize once more so
        # a malformed upstream label cannot reach the release gate or canonical data.
        run_step([python, "scripts/normalize_records.py"])
        # #371: repair proven 2026-10-12 original observation times after
        # normalization and before release/publish, in this same local writer.
        run_step([python, "scripts/reconcile_cnki_first_seen_371.py"])
        run_step([python, "scripts/enrich_china_relevance.py", "--all"])
        # Regenerate the upstream missing-abstract list so the local supplement
        # keeps it consistent with the just-updated daily/seen data.
        run_step([python, "scripts/build_upstream_missing_abstracts.py"])
        run_step([python, "scripts/product_audit.py"])
        run_step([python, "scripts/audit_recent72_coverage.py"])
        # Do not recompute the global formal-journal health here. This local
        # supplement intentionally does not fetch the English publisher RSS
        # set; recomputing it would turn untouched paths into false failures.
        # The GitHub full workflow owns data/source_health.json.
        run_step([python, "scripts/audit_formal_journal_coverage.py"])
        run_step([python, "scripts/release_gate.py"])
        run_step([python, "scripts/monitor_health.py"])
        finished_at = now()
        record_source("local-cnki-run", ok=True, count=1, message=f"finished; log={log_path_for_status()}")
        write_local_status(
            "success",
            message="全部 CNKI RSS 源已通过校验",
            count=cnki_count,
            finished_at=finished_at,
            source_health=source_health,
        )
        record_workflow_run(
            {
                "mode": "light",
                "mode_label": "本地中文补充",
                "event": "local-cnki",
                "schedule": "",
                "run_id": "",
                "run_url": "",
                "date": today_str(),
                "finished_at": finished_at,
                "updated_at": finished_at,
            }
        )
        final_status_recorded = True
        if not args.no_push:
            write_local_status(
                "publishing",
                message="数据已生成，等待推送完成",
                count=cnki_count,
                source_health=source_health,
            )
            record_source("local-cnki-publish", ok=False, count=0, message="pending")
            run_step(["git", "add", "data"])
            if git_has_staged_changes():
                run_step(["git", "commit", "-m", "Update local CNKI supplement"])
                # Git push rejects a concurrent remote update atomically; the
                # next scheduled run resets to the then-current main and
                # regenerates the data supplement.
                push_with_retries()
                write_local_status(
                    "published",
                    message="本地 CNKI 结果已推送到 origin/main",
                    count=cnki_count,
                    source_health=source_health,
                )
                record_source("local-cnki-publish", ok=True, count=1, message="published to origin/main")
                publish_final_status()
            else:
                log("No generated changes to commit.")
                write_local_status(
                    "published",
                    message="无新增数据，主线已是最新",
                    count=cnki_count,
                    source_health=source_health,
                )
                record_source("local-cnki-publish", ok=True, count=0, message="no generated changes")
                publish_final_status()

        log("Local CNKI update finished successfully.")
    except Exception as exc:  # noqa: BLE001
        write_local_status("failed", message=f"{type(exc).__name__}: {exc}")
        if not final_status_recorded:
            record_source("local-cnki-run", ok=False, count=0, message=f"{type(exc).__name__}: {exc}; log={log_path_for_status()}")
        log(f"FAILED: {type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    main()
