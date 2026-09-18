"""Render the API usage page (docs/usage/index.html).

Reads ``data/semantic_scholar_usage.json`` produced by the data line and
writes a self-contained HTML page with per-provider KPI cards (one row),
per-day request tables with stacked bars, key status, and the official usage
policy.  Times are shown in Beijing time.  Missing data renders an honest
placeholder so the page always exists.

Display-line script: only reads ``data/**`` and writes ``docs/**``.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from common import BEIJING_TZ, DATA_DIR, DOCS_DIR, html_escape, read_json, write_text

POLICY_NOTE = (
    "配额说明：Elsevier 配额按 7 天重置（按 20,000 次/周估算，预警阈值 16,000；响应若带 "
    "X-RateLimit-* 头则以官方剩余额度为准）；Semantic Scholar keyed access 当前按 1 RPS "
    "基准运行，并继续使用本仓库已接受的保守 pacing / circuit / fail-open 控制。"
    "本页每次数据更新自动刷新，来源为真实 metadata provider health；不会为凭证保活制造额外 provider 请求。"
)


def _bj(value: Any) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            BEIJING_TZ
        ).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value)


def _short_bj(value: Any) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            BEIJING_TZ
        ).strftime("%m-%d %H:%M")
    except ValueError:
        return str(value)


def _fmt(n: Any) -> str:
    return f"{int(n or 0):,}"


def _kpi_cards(prov: dict[str, Any], state_label: str) -> str:
    total = prov.get("total") if isinstance(prov.get("total"), dict) else {}
    weekly = int(prov.get("weekly_requests_7d") or 0)
    items = [
        ("Key 状态", state_label),
        ("本周请求", _fmt(weekly)),
        ("累计请求", _fmt(total.get("attempts"))),
        ("成功返回", _fmt(total.get("available"))),
        ("被限流", _fmt(total.get("rate_limited"))),
        ("最近使用", _short_bj(prov.get("last_used_at"))),
    ]
    return "".join(
        f'<div class="card"><span class="k">{html_escape(k)}</span>'
        f'<span class="v">{html_escape(v)}</span></div>'
        for k, v in items
    )


def _bar_row(row: dict[str, Any], max_attempts: int, *, els: bool) -> str:
    a = int(row.get("attempts") or 0)
    avail = int(row.get("available") or 0)
    rl = int(row.get("rate_limited") or 0)
    nf = int(row.get("not_found") or 0)
    sk = int(row.get("skipped") or 0)
    empty = int(row.get("empty") or 0)
    other = max(0, a - avail - rl - nf - sk - empty)

    def pct(v: int) -> str:
        return f"{v / a * 100:.1f}%" if a else "0%"

    segs = (
        f'<span class="s s-avail" style="width:{pct(avail)}"></span>'
        f'<span class="s s-rate" style="width:{pct(rl)}"></span>'
        f'<span class="s s-nf" style="width:{pct(nf)}"></span>'
        f'<span class="s s-skip" style="width:{pct(sk)}"></span>'
        f'<span class="s s-other" style="width:{pct(other)}"></span>'
    )
    bar = f'<div class="bar" style="width:{a / max_attempts * 100:.2f}%">{segs}</div>'
    if els:
        extra = f'<td class="num">{_fmt(empty)}</td>'
    else:
        extra = f'<td class="num">{_fmt(nf)}</td><td class="num">{_fmt(sk)}</td>'
    return (
        f"<tr><td>{html_escape(str(row.get('date') or ''))}</td>"
        f'<td class="num">{_fmt(a)}</td><td class="num">{_fmt(avail)}</td>'
        f"{extra}<td class=\"num\">{_fmt(rl)}</td><td>{bar}</td></tr>"
    )


def _provider_section(title: str, prov: dict[str, Any], state_label: str, *, els: bool) -> str:
    if not prov:
        return (
            f"<h2>{html_escape(title)}</h2>"
            "<p>该 Provider 的用量数据将在下一次数据更新后生成。</p>"
        )
    by_day = prov.get("by_day") if isinstance(prov.get("by_day"), list) else []
    max_attempts = max([int(r.get("attempts") or 0) for r in by_day] + [1])
    rows = "".join(_bar_row(r, max_attempts, els=els) for r in sorted(by_day, key=lambda x: x.get("date") or ""))
    head_cols = (
        "<th>日期</th><th>请求</th><th>成功</th><th>未找到</th><th>跳过</th><th>限流</th><th>构成</th>"
        if not els
        else "<th>日期</th><th>请求</th><th>成功</th><th>空响应</th><th>限流</th><th>构成</th>"
    )
    legend = (
        '<div class="leg"><span><i class="lg-avail"></i>成功</span>'
        '<span><i class="lg-rate"></i>限流</span>'
        '<span><i class="lg-nf"></i>未找到</span>'
        '<span><i class="lg-skip"></i>跳过</span></div>'
        if not els
        else '<div class="leg"><span><i class="lg-avail"></i>成功</span>'
        '<span><i class="lg-empty"></i>空响应</span></div>'
    )
    return (
        f"<h2>{html_escape(title)} <span class=\"pill\">已配置</span></h2>"
        f'<div class="cards">{_kpi_cards(prov, state_label)}</div>'
        f"{legend}"
        '<div class="table-wrap"><table><thead><tr>'
        f"{head_cols}</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def render_usage(data_dir: Path = DATA_DIR, docs_dir: Path = DOCS_DIR) -> Path:
    usage = read_json(data_dir / "semantic_scholar_usage.json", {})
    if not isinstance(usage, dict) or not usage:
        body = "<p>用量数据尚未生成。数据更新流程首次运行后本页会自动填充。</p>"
        updated = "—"
        ss_prov: dict[str, Any] = {}
        els_prov: dict[str, Any] = {}
    else:
        providers = usage.get("providers") if isinstance(usage.get("providers"), dict) else {}
        ss_prov = providers.get("semantic-scholar") if isinstance(providers.get("semantic-scholar"), dict) else {}
        els_prov = providers.get("elsevier") if isinstance(providers.get("elsevier"), dict) else {}
        if not ss_prov and usage.get("total") is not None:
            # Backward-compatible fallback for the pre-#86 usage payload.
            ss_prov = {
                "api_key_configured": usage.get("key_configured"),
                "last_used_at": usage.get("last_used_at"),
                "weekly_requests_7d": None,
                "total": usage.get("total"),
                "by_day": usage.get("by_day"),
            }
        updated = _bj(usage.get("updated_at"))
        body = _provider_section("Semantic Scholar", ss_prov, "已配置", els=False)
        body += _provider_section("Elsevier", els_prov, "APIKey + InstToken", els=True)

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="index,follow">
<title>学术 API 用量</title>
<style>
  :root {{ --paper:#fafafa; --ink:#1f2430; --muted:#6b7280; --line:#e5e7eb; --blue:#2563eb; --avail:#16a34a; --rate:#dc2626; --nf:#d97706; --skip:#9ca3af; --empty:#eab308; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.6; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:28px 20px 64px; }}
  a {{ color:var(--blue); text-decoration:none; }}
  h1 {{ font-size:26px; margin:12px 0 4px; }}
  .sub {{ color:var(--muted); font-size:14px; margin:0 0 20px; }}
  h2 {{ font-size:19px; margin:28px 0 12px; }}
  .pill {{ font-size:12px; color:#16a34a; font-weight:600; }}
  .cards {{ display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin:16px 0; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:10px 12px; display:flex; flex-direction:column; gap:3px; min-width:0; }}
  .card .k {{ color:var(--muted); font-size:12px; }}
  .card .v {{ font-size:16px; font-weight:600; font-variant-numeric:tabular-nums; word-break:break-all; }}
  .leg {{ display:flex; gap:14px; font-size:12px; color:var(--muted); margin-bottom:8px; }}
  .leg i {{ width:10px; height:10px; border-radius:3px; display:inline-block; margin-right:4px; vertical-align:-1px; }}
  .lg-avail {{ background:var(--avail); }} .lg-rate {{ background:var(--rate); }}
  .lg-nf {{ background:var(--nf); }} .lg-skip {{ background:var(--skip); }} .lg-empty {{ background:var(--empty); }}
  .table-wrap {{ overflow-x:auto; background:#fff; border:1px solid var(--line); border-radius:10px; }}
  table {{ border-collapse:collapse; width:100%; min-width:680px; font-size:13px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }}
  th {{ background:#f3f4f6; font-weight:600; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .bar {{ height:14px; border-radius:7px; overflow:hidden; display:flex; background:#f3f4f6; min-width:2px; }}
  .s {{ height:100%; }}
  .s-avail {{ background:var(--avail); }} .s-rate {{ background:var(--rate); }}
  .s-nf {{ background:var(--nf); }} .s-skip {{ background:var(--skip); }} .s-other {{ background:#d1d5db; }}
  .note {{ margin-top:24px; padding:12px 14px; background:#fff7ed; border:1px solid #fed7aa; border-radius:10px; font-size:13px; color:#7c2d12; line-height:1.6; }}
  footer {{ margin-top:28px; color:var(--muted); font-size:12px; }}
  @media (max-width: 900px) {{ .cards {{ grid-template-columns:repeat(3,1fr); }} }}
</style>
</head>
<body>
<div class="wrap">
  <p><a href="../index.html">← 返回首页</a></p>
  <h1>学术 API 用量</h1>
  <p class="sub">更新时间：{html_escape(updated)} 北京时间</p>
  {body}
  <div class="note">{POLICY_NOTE}</div>
  <footer>数据来源：<code>data/semantic_scholar_usage.json</code>（由数据线每日生成）。</footer>
</div>
</body>
</html>
"""
    out = docs_dir / "usage" / "index.html"
    write_text(out, html)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    args = parser.parse_args(argv)

    out = render_usage(args.data_dir, args.docs_dir)
    print(f"rendered {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())