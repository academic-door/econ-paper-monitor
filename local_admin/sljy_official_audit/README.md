# SLJY first-party source observation (② owner-local)

**Status:** isolated read-only local audit adapter. No production Daily/seen writes and no publisher-online-first universe certification.

This task runs only on the authorized Windows machine, from a stable **non-sync** directory:
`D:\AcademicDoor-LocalAudits\sljy-official\`.

- Install a **dedicated sparse audit clone**, never point the runner at the E: pinned working tree or C: production runner:
  `gh repo clone academic-door/econ-paper-monitor D:\AcademicDoor-LocalAudits\sljy-official\repo -- --depth=1 --filter=blob:none --sparse`
  then `git -C D:\AcademicDoor-LocalAudits\sljy-official\repo sparse-checkout set scripts data/daily`.
- Copy the reviewed `local_admin/sljy_official_audit/run.py` from **merged current main** to `D:\AcademicDoor-LocalAudits\sljy-official\run.py`. This file is not installed in the pinned production checkout.
- Run with the machine's Python: `python D:\AcademicDoor-LocalAudits\sljy-official\run.py`. It synchronizes only its own clone with GitHub main (requires exact SHA parity), calls the first-party HTTPS/TLS-verified two-page auditor, and writes local observation evidence.
- If scheduled, use a **separate** Windows task from `Econ Papers Daily - Local Supplement`; recommended four daily triggers 01:00/07:00/13:00/19:00 machine-local time (offset from the existing 00:10/06:10/12:10/18:10 CNKI writer). Disallow parallel instances and retain the per-process lock.
- Evidence: `evidence/observation-<UTC>.json` (bounded to 120), `evidence/latest.json` (includes failures), and `evidence/last_accepted.json` (only updated upon independent first-party observation + canonical print-issue reconciliation). Preserve `observed_at` when failures happen; do not refresh last accepted snapshot metadata.
- The run's Task Scheduler success only indicates that its evidence was safely persisted. Trust predicates are explicit in the JSON: `run_status`, `print_issue_roster_status`, `online_first_coverage_status=NOT_VERIFIED`, and `canonical_commit`. A failed GitHub fetch, blocked publisher access, conflicting official pages, changed print issue, or archive mismatch **must not** be silently green.
- No Git push, paid provider, unauthenticated proxy, TLS bypass, publisher password, non-owner workspace mutation, live GitHub Writer dispatch, or changes to the production `source_groups.cn-journals` 5/6 truth. This evidence is a separate local first-party observation lane, not GitHub Runner transport recovery.
- The publisher HTML need not be uploaded/persisted (we retain independently checkable page SHA256 and publisher-article IDs); no copyrighted full text is stored.

**Acceptance before enabling automatic schedules:** exact PR-head CI; independent current-main code readback; two *separate* live local observations on the dedicated sparse clone; a fail-closed test proving `last_accepted.json` is unchanged; separate scheduled task registration and successful manual trigger; and a durable #132 evidence pointer. If the root cannot be made non-sync, clone/credential checks fail, or the scheduler cannot be registered safely, do not change the existing producer; document the natural blocker.

This is an **opt-in operational adapter**, not an organization-wide crawler. Parent Decision 0023 applies: `PIPELINE_HEALTHY` does not imply `SOURCE_RECONCILED_AS_OF`, and a printed-issue roster never certifies the unobserved all-online-first universe.
