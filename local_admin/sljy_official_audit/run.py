"""Isolated, scheduled read-only SLJY publisher observation on authorized Windows.

The task owns only <install_root>/repo (a non-sync, sparse *audit* clone)
and <install_root>/evidence. It never touches E: pinned or C: producer
workspaces and never changes canonical Daily/seen or production source status.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY = "academic-door/econ-paper-monitor"
MAX_EVIDENCE = 120


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def command(argv: list[str], *, timeout: int = 180) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        raise RuntimeError(f"{argv[0]} operation failed (exit={result.returncode}): "
                           + result.stderr.strip()[-400:])
    return result.stdout.strip()


@contextlib.contextmanager
def exclusive_lock(path: Path):
    """A process-owned file lock releases on crash; never delete a live lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        # On Windows a second handle cannot read the locked byte; inspect
        # only the file length before attempting the nonblocking lock.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"x")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("audit already running") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("audit already running") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=".__sljy_", suffix=".tmp", delete=False,
    ) as output:
        json.dump(record, output, indent=2, sort_keys=True, ensure_ascii=False)
        output.write("\n")
        temp_path = Path(output.name)
    os.replace(temp_path, path)


def refresh_canonical_clone(repo: Path) -> str:
    """Only mutate the dedicated audit clone; never use producer worktrees."""
    if not (repo / ".git").exists():
        raise RuntimeError("dedicated sparse audit clone is absent; provisioning required")
    if not (repo / ".git").is_dir():
        raise RuntimeError("audit repo must be a standalone clone, not a worktree")
    origin = command(["git", "-C", str(repo), "remote", "get-url", "origin"])
    if not origin.endswith("/academic-door/econ-paper-monitor.git") and not origin.endswith(
        "academic-door/econ-paper-monitor"
    ):
        raise RuntimeError("audit clone remote does not match owner repository")
    command(["git", "-C", str(repo), "fetch", "--depth=1", "--no-tags", "origin", "main"], timeout=240)
    # This reset is permitted ONLY in the dedicated disposable audit clone.
    command(["git", "-C", str(repo), "reset", "--hard", "FETCH_HEAD"], timeout=180)
    sha = command(["git", "-C", str(repo), "rev-parse", "HEAD"])
    current = command([
        "gh", "api", f"repos/{REPOSITORY}/git/ref/heads/main",
        "--jq", ".object.sha",
    ], timeout=25)
    if sha != current:
        raise RuntimeError("main advanced while audit snapshot was refreshing; no current parity")
    if not (repo / "scripts" / "audit_sljy_official.py").is_file():
        raise RuntimeError("first-party audit code missing from sparse checkout")
    if not (repo / "data" / "daily").is_dir():
        raise RuntimeError("canonical Daily not present in sparse audit checkout")
    return sha


def evaluate(root: Path) -> dict[str, Any]:
    repo = root / "repo"
    evidence = root / "evidence"
    checked_at = utc_now()
    record: dict[str, Any] = {
        "checked_at": checked_at,
        "executor": "WINDOWS_AUTHORIZED_LOCAL_READ_ONLY",
        "repository": REPOSITORY,
        "publisher_transport": "LOCAL_FIRST_PARTY_HTTPS",
        "source_scope": "PUBLISHER_LISTED_CURRENT_PRINT_ISSUE_ONLY",
        "online_first_coverage_status": "NOT_VERIFIED",
        "writer_mutation": False,
    }
    try:
        sha = refresh_canonical_clone(repo)
        record["canonical_commit"] = sha
        result = subprocess.run(
            [
                sys.executable, str(repo / "scripts" / "audit_sljy_official.py"),
                "--daily-dir", str(repo / "data" / "daily"),
            ],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=150, check=False,
            cwd=str(repo),
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        try:
            audit = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"auditor returned no JSON (exit={result.returncode}): "
                + result.stderr.strip()[-450:]
            ) from exc
        record["audit"] = audit
        accepted = (
            result.returncode == 0
            and audit.get("print_issue_roster_status") == "RECONCILED_AS_OF"
            and audit.get("online_first_coverage_status") == "NOT_VERIFIED"
            and bool(audit.get("observed_at"))
        )
        record["run_status"] = "OBSERVED_PRINT_ISSUE_RECONCILED" if accepted else "VERIFICATION_BLOCKED_OR_DIFF"
        record["success"] = accepted
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        record["run_status"] = "VERIFICATION_BLOCKED"
        record["success"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["finished_at"] = utc_now()
    # Evidence is local to this isolated installation; no git push or canonical writes.
    unique_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    atomic_json(evidence / f"observation-{unique_stamp}.json", record)
    atomic_json(evidence / "latest.json", record)
    if record["success"]:
        atomic_json(evidence / "last_accepted.json", record)
    archives = sorted(evidence.glob("observation-*.json"))
    for old in archives[:-MAX_EVIDENCE]:
        old.unlink()
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent,
                        help="Dedicated non-sync audit installation; NOT a producer checkout")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 2 on an observed source block/diff (used in acceptance tests)")
    args = parser.parse_args()
    root = args.root.resolve()
    # Never allow the audit updater to point at C: production or E: pinned.
    if os.name == "nt" and root.drive.upper() != "D:":
        raise SystemExit("audit installation must be on the approved isolated D: drive")
    try:
        with exclusive_lock(root / "audit.lock"):
            result = evaluate(root)
    except RuntimeError as exc:
        print(json.dumps({"run_status": "SKIPPED_LOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 0
    print(json.dumps({
        "run_status": result["run_status"],
        "success": result["success"],
        "canonical_commit": result.get("canonical_commit"),
        "observed_at": result.get("audit", {}).get("observed_at"),
        "print_issue_roster_status": result.get("audit", {}).get("print_issue_roster_status"),
        "online_first_coverage_status": result["online_first_coverage_status"],
    }, ensure_ascii=False))
    # Scheduled task success = evidence persisted; source status is in the record.
    return 2 if args.strict and not result["success"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
