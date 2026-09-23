"""Render the production static public site into docs/."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from common import BEIJING_TZ, DATA_DIR, DOCS_DIR, html_escape, load_journals, normalize_doi, read_json, today_str, write_text
from dedupe import record_match_keys
from status import load_status
from display_contract import display_titles
from public_topics import TOPIC_RULES, article_topic_codes


SITE_NAME = "Econ Papers Daily"
SITE_SUBTITLE = "每日追踪 TOP 经济学期刊论文"
BASE = "__BASE__"
CN_TZ = BEIJING_TZ
# The worker is deployed by .github/workflows/deploy-presence.yml.  Keeping a
# public default makes the small status indicator work on Pages even when the
# optional repository variable has not been added yet; request failures remain
# silent and never affect paper rendering.
DEFAULT_PRESENCE_ENDPOINT = "https://econ-paper-monitor-presence.academic-door.workers.dev/presence"
LAZY_DATASETS: dict[str, tuple[list[dict[str, Any]], str]] = {}
LAZY_SHARD_SIZE = 40
ROUTE_BUCKETS = 256
ROUTE_SKIP_SHARD_LIMIT = 4

CHINA_TITLE_PATTERNS = [
    r"\bchina\b",
    r"\bchinese\b",
    r"\bmainland china\b",
    r"\bhong kong\b",
    r"\btaiwan\b",
    r"\bbeijing\b",
    r"\bshanghai\b",
    r"\bguangdong\b",
    r"\bhukou\b",
]

FIELD_LABELS = {
    "general": "综合",
    "development": "发展经济学",
    "agriculture_environment_resource": "农业/环境/资源",
    "applied_empirical": "应用实证",
    "macroeconomics": "宏观经济学",
    "finance": "金融",
    "econometrics": "计量经济学",
    "environmental": "环境经济学",
    "labor": "劳动经济学",
    "international": "国际经济学",
    "international_trade": "国际贸易",
    "macro": "宏观经济学",
    "china": "中国经济",
    "public_political": "公共/政治经济学",
    "theory": "经济理论",
    "economic_history": "经济史",
    "industrial_organization": "产业组织",
    "game_theory": "博弈论",
    "microeconomics": "微观经济学",
    "population": "人口经济学",
    "urban": "城市经济学",
    "behavior_organization": "行为/组织",
    "law_comparative": "法律/比较制度",
    "experimental": "实验经济学",
    "chinese": "中文期刊",
}

TOPIC_LABELS = {
    "china": "与中国相关",
    "agriculture": "农业与食品",
    "environment": "环境与气候",
    "development": "发展经济学",
    "finance": "金融",
    "macro": "宏观与货币",
    "labor": "劳动",
    "public": "公共与政治经济学",
    "trade": "国际贸易",
    "urban": "城市与区域",
    "econometrics": "计量方法",
    "theory": "理论与博弈",
    "behavior": "行为与组织",
    "health": "健康",
    "education": "教育",
    "firms": "企业与产业",
    "inequality": "不平等",
    "history": "经济史",
}

STYLE = """
:root{color-scheme:light;--ink:#1f2328;--muted:#656d76;--line:#d0d7de;--soft:#f6f8fa;--page:#fafafa;--panel:#fff;--blue:#0969da;--blue-soft:#ddf4ff;--red:#cf222e;--red-soft:#fff1f0;--shadow:0 1px 2px rgba(31,35,40,.05)}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;line-height:1.55}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.skip-link{position:absolute;left:16px;top:-48px;z-index:10;background:var(--blue);color:#fff;border-radius:7px;padding:9px 12px}.skip-link:focus{top:12px;text-decoration:none}
.shell{display:grid;grid-template-columns:320px minmax(0,1120px);width:min(100% - 32px,1440px);margin:0 auto;min-height:100vh;background:#fff;border-left:1px solid var(--line);border-right:1px solid var(--line)}.sidebar{background:var(--soft);border-right:1px solid var(--line);padding:24px;position:sticky;top:0;height:100vh;overflow:auto}.brand{font-size:22px;font-weight:800;margin:0}.subtitle{color:var(--muted);font-size:14px;margin:4px 0 22px}
.side-block{margin:22px 0}.side-title{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;margin-bottom:8px}.side-link{display:flex;justify-content:space-between;gap:12px;border-radius:6px;padding:7px 9px;color:var(--ink);font-size:14px}.side-link:hover{background:#fff;text-decoration:none}.side-main{min-width:0}.side-main strong{display:block;white-space:normal}.side-main em{display:block;color:var(--muted);font-style:normal;font-size:12px;line-height:1.35;margin-top:1px}.count{flex:0 0 auto;color:var(--muted)}
.content{min-width:0;background:var(--page)}.topbar{border-bottom:1px solid var(--line);border-top:3px solid var(--blue);background:#fff}.topbar-inner{max-width:1120px;margin:0 auto;padding:16px 28px;display:flex;justify-content:space-between;align-items:center;gap:20px}.nav a{margin-left:18px;color:var(--muted);font-size:14px}.nav a.active,.nav a:hover{color:var(--blue);text-decoration:none}.wrap{max-width:1120px;margin:0 auto;padding:26px 28px 48px}
.banner{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:linear-gradient(180deg,#fff 0%,#f8fbff 100%);box-shadow:var(--shadow)}.banner-main{padding:34px 36px 30px}.hero-layout{display:grid;grid-template-columns:minmax(0,1fr) 188px;gap:28px;align-items:center}.eyebrow{color:var(--blue);font-size:14px;font-weight:800;letter-spacing:0;margin:0 0 8px}.banner h1{font-family:Georgia,"Times New Roman",serif;font-size:46px;line-height:1.06;margin:0 0 12px}.banner p{color:var(--muted);font-size:19px;max-width:720px;margin:0}.hero-stats{display:grid;grid-template-columns:repeat(3,minmax(150px,1fr));gap:12px;margin-top:24px;max-width:860px}.hero-stat{border-top:3px solid var(--blue);background:#fff;border-radius:8px;padding:12px 13px;box-shadow:var(--shadow);color:var(--ink)}.hero-stat:hover{text-decoration:none;box-shadow:0 0 0 1px var(--blue)}.hero-stat.china{border-top-color:var(--red)}.hero-stat strong{display:block;font-size:27px;line-height:1.05}.hero-stat span{color:var(--muted);font-size:13px}.hero-stat.duo{display:block}.hero-stat.duo .stat-title{display:block;color:var(--muted);font-size:13px;margin-bottom:8px}.hero-stat-pair{display:grid;grid-template-columns:1fr 1fr;gap:10px}.hero-stat-pair strong{font-size:25px}.hero-stat-pair em{display:block;color:var(--muted);font-style:normal;font-size:13px;line-height:1.2}.operator-card{border:1px solid var(--line);border-radius:10px;background:#fff;padding:14px;box-shadow:var(--shadow);text-align:center;align-self:center}.operator-card img{display:block;width:128px;height:128px;object-fit:cover;margin:0 auto 10px;border-radius:6px}.operator-card strong{display:block;font-size:16px}.operator-card span{display:block;color:var(--ink);font-size:13px;font-weight:700;margin-top:3px}.operator-card em{display:block;color:var(--red);font-style:normal;font-size:12px;font-weight:800;margin-top:4px}.operator-line{margin-top:18px;color:var(--muted);font-size:13px}.operator-line strong{color:var(--ink)}.status-strip{display:flex;gap:14px;flex-wrap:wrap;border:1px solid var(--line);border-radius:8px;background:#fff;padding:9px 12px;margin:14px 0 0;color:var(--muted);font-size:13px}.status-strip strong{color:var(--ink);font-weight:700}.status-strip .warn strong{color:#7d4e00}.stats{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 18px}.stat{display:flex;align-items:baseline;gap:8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);padding:10px 12px;color:var(--ink);box-shadow:var(--shadow)}.stat.china{border-top:3px solid var(--red)}.stat:hover{border-color:var(--blue);text-decoration:none}.stat strong{display:inline;font-size:22px;line-height:1}.stat span{font-size:13px;color:var(--muted)}
.live-count{font-size:14px;color:var(--muted);font-weight:500}.live-count .num{color:var(--red);font-weight:800}
.toolbar{display:grid;grid-template-columns:minmax(170px,1.05fr) minmax(190px,1.45fr) minmax(125px,.75fr) minmax(118px,.65fr) minmax(118px,.65fr) minmax(118px,.65fr) auto auto;gap:9px;align-items:center;margin:18px 0 8px}.control{border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--muted);padding:8px 10px;font-size:14px;min-height:38px;min-width:0}.control:focus{outline:2px solid rgba(9,105,218,.16);border-color:var(--blue)}.control.primary{background:var(--blue);border-color:var(--blue);color:#fff;font-weight:600;white-space:nowrap}.control.rss-link{color:var(--ink);background:#fff;border-color:var(--line);font-weight:600;white-space:nowrap}.control.toggle{white-space:nowrap}.control.toggle.active{background:var(--red-soft);border-color:#ffccc7;color:var(--red);font-weight:700}
.section-head{display:flex;align-items:end;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding-bottom:10px;margin-top:26px}.section-head.split-section{margin-top:58px}.section-head h2{font-size:20px;margin:0}.section-head p{margin:0;color:var(--muted);font-size:14px}
.event{position:relative;display:grid;grid-template-columns:78px minmax(0,1fr);gap:18px;border:1px solid transparent;border-bottom-color:var(--line);border-radius:8px;padding:16px 14px 16px 18px;background:transparent}.event:before{content:"";position:absolute;left:0;top:14px;bottom:14px;width:3px;border-radius:3px;background:#b6d7ff}.event[data-china="true"]:before{background:var(--red)}.event:hover{background:#fff;border-color:var(--line);box-shadow:var(--shadow)}.event:hover:before{background:var(--blue)}.event[data-china="true"]:hover:before{background:var(--red)}.event[hidden]{display:none}.time{font-weight:700;color:var(--blue);font-size:14px}.date-note{color:var(--muted);font-size:12px;margin-top:2px}.event h3{font-size:18px;line-height:1.35;margin:0 0 5px}.title-original{color:#3b434c;font-size:15px;margin:0 0 7px}.authors{color:var(--muted);margin:0 0 10px}.meta-block{display:grid;gap:6px;color:var(--muted);font-size:13px}.meta-line{display:flex;gap:8px;align-items:flex-start;min-height:24px}.meta-values{display:flex;flex-wrap:wrap;gap:8px;align-items:center;min-width:0;line-height:24px}.meta-label{color:var(--ink);font-weight:700;flex:0 0 72px;line-height:24px}.journal-chip{background:var(--blue-soft);border:1px solid #b6e3ff;color:#0550ae;border-radius:999px;padding:2px 8px;line-height:18px}.source-chip{color:var(--muted)}.date-chip{display:inline-flex;align-items:center;border:1px solid #b6e3ff;background:var(--blue-soft);color:#0550ae;border-radius:999px;padding:2px 8px;line-height:18px}.date-chip.pending{border-color:#f0d98c;background:#fff8c5;color:#7d4e00}.date-chip.issue{border-color:var(--line);background:var(--soft);color:var(--muted)}.pill{border:1px solid var(--line);background:var(--soft);border-radius:999px;padding:2px 7px;line-height:18px}.pill.fresh{background:#dafbe1;border-color:#aceebb;color:#116329}.pill.lag{background:#fff8c5;border-color:#f0d98c;color:#7d4e00}.pill.china{background:var(--red-soft);border-color:#ffccc7;color:var(--red);font-weight:800}.doi{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;line-height:24px;word-break:break-word}
.journal-table{width:100%;border-collapse:collapse;margin-top:16px;font-size:14px}.journal-table th,.journal-table td{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}.journal-table th{background:var(--soft);font-weight:700}.muted{color:var(--muted)}.empty{border:1px dashed var(--line);border-radius:8px;padding:20px;color:var(--muted);background:var(--soft)}.home-note{padding:14px 16px;font-size:14px}.archive-list{padding-left:18px}.archive-list li{margin:8px 0}.view-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.view-tab{border:1px solid var(--line);border-radius:999px;background:#fff;padding:7px 11px;color:var(--ink);font-size:14px}.view-tab:hover{text-decoration:none;border-color:var(--blue)}.view-tab.active{background:var(--blue);border-color:var(--blue);color:#fff}.source-status{display:inline-flex;border-radius:999px;border:1px solid var(--line);padding:2px 8px;font-size:12px;font-weight:700;background:var(--soft);white-space:nowrap}.source-status.ok{background:#dafbe1;border-color:#aceebb;color:#116329}.source-status.todo{background:#fff8c5;border-color:#f0d98c;color:#7d4e00}.source-status.pause{background:var(--red-soft);border-color:#ffccc7;color:var(--red)}
.audit-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}.audit-card{border:1px solid var(--line);border-radius:8px;background:#fff;padding:14px;box-shadow:var(--shadow)}.audit-card strong{display:block;font-size:26px}.audit-list{display:grid;gap:12px}.audit-item{border:1px solid var(--line);border-radius:8px;background:#fff;padding:14px}.audit-item h3{font-size:16px;margin:0 0 6px}.audit-meta{color:var(--muted);font-size:13px}.audit-reason{margin-top:8px;color:#3b434c;font-size:14px}.gate{max-width:620px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:24px;box-shadow:var(--shadow)}.gate input{width:100%;border:1px solid var(--line);border-radius:7px;padding:10px;margin:12px 0}.gate button{border:1px solid var(--blue);background:var(--blue);color:#fff;border-radius:7px;padding:9px 12px}.gate-note{color:var(--muted);font-size:13px}.hidden{display:none!important}
@media(max-width:1100px){.toolbar{grid-template-columns:minmax(180px,1fr) minmax(220px,1.4fr) minmax(140px,.8fr) minmax(130px,.7fr);}.toolbar .control.toggle,.toolbar .control.primary{width:max-content}}
@media(max-width:920px){.shell{display:block;width:100%;border:0}.sidebar{position:static;height:auto}.topbar-inner{display:block}.nav{margin-top:10px}.nav a{margin:0 16px 0 0}.banner h1{font-size:36px}.banner p{font-size:17px}.banner-main{padding:30px 24px}.hero-layout{grid-template-columns:1fr}.operator-card{max-width:210px;text-align:left;display:grid;grid-template-columns:92px 1fr;gap:12px;align-items:center}.operator-card img{width:92px;height:92px;margin:0}.hero-stats{grid-template-columns:1fr}.toolbar{grid-template-columns:1fr}.toolbar .control.toggle,.toolbar .control.primary{width:100%}.event{grid-template-columns:1fr}.audit-grid{grid-template-columns:1fr}}
"""

EXTRA_STYLE = """
.nav{display:flex;justify-content:flex-end;gap:18px}.nav a{margin-left:0}
.presence{display:inline-flex;align-items:center;gap:5px;color:var(--muted);font-size:12px;white-space:nowrap}.presence-dot{color:#1a9b52;font-size:14px}
"""

SECONDARY_STYLE = """
:root{color-scheme:light;--paper:#f4f1ea;--ink:#202426;--muted:#6f716d;--line:#d8d3c9;--blue:#1d5f83;--blue-deep:#16445e;--red:#a94236;--white:#fbfaf7;--soft:#ece7dc;--mono:"IBM Plex Mono",Consolas,monospace;--serif:Georgia,"Noto Serif SC","Songti SC",serif;--sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.62}a{color:inherit;text-decoration:none}a:hover{color:var(--blue)}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #84b8d3;outline-offset:3px}.skip-link{position:absolute;left:1rem;top:-3rem;background:var(--blue-deep);color:#fff;padding:.6rem 1rem;z-index:5}.skip-link:focus{top:1rem}.site-header{position:sticky;top:0;z-index:4;border-bottom:1px solid var(--line);background:rgba(244,241,234,.94);backdrop-filter:blur(12px)}.header-inner{max-width:1280px;margin:auto;padding:1rem 2rem;display:flex;align-items:center;gap:2rem}.wordmark{font-family:var(--serif);font-size:1.2rem;line-height:1.05;white-space:nowrap}.wordmark small{display:block;font:600 .66rem var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--blue)}.nav{display:flex;gap:1.35rem;margin-left:auto;font-size:.88rem;color:var(--muted);align-items:center}.nav a.active{color:var(--blue);font-weight:700}.presence{font-size:.76rem;white-space:nowrap;color:var(--muted)}.presence::before{content:"";display:inline-block;width:.45rem;height:.45rem;border-radius:50%;background:#3e8b61;margin-right:.4rem;vertical-align:1px}.presence-dot{display:none}.menu{display:none;border:0;background:transparent;color:var(--ink);font-size:1.35rem;min-width:44px;min-height:44px}.secondary-page{max-width:1280px;margin:auto;padding:0 2rem}.page-hero{padding:4.7rem 0 2.4rem;border-bottom:1px solid var(--ink);display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2rem;align-items:end}.page-eyebrow{font:600 .72rem var(--mono);letter-spacing:.15em;color:var(--blue);text-transform:uppercase;margin:0 0 .85rem}.page-hero h1{font:400 clamp(2.6rem,6vw,5.6rem)/.94 var(--serif);letter-spacing:-.03em;margin:0;overflow-wrap:anywhere}.page-hero p{max-width:680px;color:var(--muted);margin:.95rem 0 0;font:400 1.05rem/1.55 var(--serif)}.context-nav{display:flex;gap:.55rem;flex-wrap:wrap;padding:1rem 0 0}.context-nav a{border:1px solid var(--line);min-height:36px;padding:.42rem .7rem;font-size:.78rem;color:var(--muted);display:inline-flex;align-items:center}.context-nav a:hover,.context-nav a.active{border-color:var(--blue);color:var(--blue);background:rgba(29,95,131,.05)}.wrap{padding:2.5rem 0 4rem}.section-head{display:flex;align-items:end;justify-content:space-between;gap:1.5rem;border-bottom:1px solid var(--line);padding:2.2rem 0 1rem;margin:0}.section-head.split-section{margin-top:3rem}.section-head h2{font:400 clamp(1.7rem,3vw,2.5rem)/1.05 var(--serif);margin:0}.section-head p{margin:.45rem 0 0;color:var(--muted);font-size:.86rem;max-width:720px}.stats,.audit-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:1.3rem 0;background:var(--line)}.stat,.audit-card{background:var(--paper);padding:1rem .9rem;color:var(--muted);min-width:0}.stat:hover{background:rgba(251,250,247,.55);text-decoration:none}.stat strong,.audit-card strong{display:block;color:var(--ink);font:400 1.65rem/1 var(--serif);overflow-wrap:anywhere}.stat span,.audit-card span{display:block;font-size:.78rem;margin-top:.25rem}.stat.china strong,.pill.china{color:var(--red)}.toolbar{display:flex;gap:.5rem;flex-wrap:nowrap;align-items:center;margin:1.35rem 0;min-width:0}.control{min-height:40px;border:1px solid var(--line);background:var(--white);color:var(--muted);padding:.45rem .6rem;font:500 .8rem var(--sans);max-width:100%;flex:0 1 auto;min-width:0;transition:border-color .2s ease,box-shadow .2s ease,background .2s ease,color .2s ease}.toolbar input[data-filter-role="search"]{flex:1 1 150px;min-width:110px}.toolbar .more-filters{position:relative;display:flex;align-items:center;flex:0 0 auto}.toolbar .more-filters summary{list-style:none;cursor:pointer;padding:.45rem .6rem;white-space:nowrap;font-weight:500;color:var(--muted)}.toolbar .more-filters summary::-webkit-details-marker{display:none}.toolbar .more-filters[open] summary{border-bottom:1px solid var(--line);color:var(--blue-deep)}.toolbar .more-filters .more-filters-row{position:absolute;right:0;top:100%;z-index:5;display:flex;flex-wrap:wrap;gap:.5rem;padding:.6rem;min-width:300px;max-height:70vh;overflow-y:auto;background:var(--white);border:1px solid var(--line);box-shadow:0 10px 24px rgba(32,36,38,.1)}.toolbar .more-filters .more-filters-row .control{min-height:38px}.control:focus{border-color:var(--blue);box-shadow:0 0 0 2px rgba(29,95,131,.14);outline:0}.control.primary,.control.toggle.active{background:var(--blue);border-color:var(--blue);color:#fff}.control.rss-link{font-weight:700}.view-tabs{display:flex;gap:.55rem;flex-wrap:wrap;margin:1rem 0}.view-tab{border:1px solid var(--line);padding:.5rem .75rem;color:var(--muted);font-size:.82rem;min-height:38px;display:inline-flex;align-items:center}.view-tab.active,.view-tab:hover{border-color:var(--blue);color:var(--blue);background:rgba(29,95,131,.05)}.event{position:relative;display:grid;grid-template-columns:78px minmax(0,1fr);gap:1.4rem;padding:1.65rem 0;border-bottom:1px solid var(--line);background:transparent;min-width:0}.event:before{content:"";position:absolute;left:87px;top:1.95rem;width:8px;height:8px;border:1px solid #9eb7c3;border-radius:50%;background:var(--paper)}.event:after{content:"";position:absolute;left:91px;top:2.6rem;bottom:-.4rem;width:1px;background:rgba(29,95,131,.2)}.event:last-of-type:after{display:none}.event[data-china="true"]:before{border-color:var(--red)}.event:hover:before{background:var(--blue);border-color:var(--blue)}.event[data-china="true"]:hover:before{background:var(--red);border-color:var(--red)}.event[hidden]{display:none}.time{font:600 .76rem var(--mono);color:var(--blue);padding-top:.24rem}.date-note{font:.68rem var(--mono);color:var(--muted);margin-top:.2rem}.event h3{font:400 clamp(1.25rem,2vw,1.85rem)/1.25 var(--serif);margin:0 0 .45rem;max-width:920px;overflow-wrap:anywhere}.title-original{font:400 .96rem/1.5 var(--serif);color:var(--muted);margin:0 0 .5rem;overflow-wrap:anywhere}.authors{font-size:.85rem;color:var(--muted);margin:0 0 .95rem;overflow-wrap:anywhere}.meta-block{display:grid;gap:.42rem;font-size:.78rem;color:var(--muted);min-width:0}.meta-line{display:flex;gap:.65rem;align-items:flex-start;min-width:0}.meta-label{flex:0 0 4.5rem;color:var(--ink);font-weight:700}.meta-values{display:flex;flex-wrap:wrap;gap:.45rem;min-width:0}.journal-chip,.date-chip,.pill,.source-status{border:1px solid var(--line);background:rgba(251,250,247,.7);color:var(--muted);padding:.14rem .45rem;font-size:.72rem;line-height:1.4;overflow-wrap:anywhere}.journal-chip{color:var(--blue-deep);border-color:#a8c0ca}.date-chip.pending{border-color:#d3bd70;color:#7d4e00}.date-chip.issue{background:var(--soft)}.pill.fresh{border-color:#92bd9e;color:#286942}.pill.lag{border-color:#d3bd70;color:#7d4e00}.doi{font-family:var(--mono);word-break:break-word}.empty{border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:2.2rem 0;color:var(--muted);background:transparent}.home-note{padding:1rem 0}.journal-table{width:100%;border-collapse:collapse;margin-top:1.3rem;font-size:.86rem}.journal-table th,.journal-table td{border-bottom:1px solid var(--line);padding:.78rem .65rem;text-align:left;vertical-align:top;overflow-wrap:anywhere}.journal-table th{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700}.muted{color:var(--muted)}.archive-list{padding-left:1.1rem}.archive-list li{margin:.45rem 0}.audit-list{display:grid;gap:0;border-top:1px solid var(--line)}.audit-item{border-bottom:1px solid var(--line);padding:1.3rem 0}.audit-item h3{font:400 1.3rem/1.3 var(--serif);margin:0 0 .45rem}.audit-meta,.audit-reason{color:var(--muted);font-size:.84rem;margin-top:.35rem}.gate{max-width:640px;border-top:1px solid var(--ink);border-bottom:1px solid var(--line);padding:1.6rem 0}.gate input{width:100%;border:1px solid var(--line);background:var(--white);min-height:44px;padding:.65rem;margin:.8rem 0}.gate button{border:1px solid var(--blue);background:var(--blue);color:#fff;min-height:42px;padding:.55rem .85rem}.gate-note{color:var(--muted);font-size:.82rem}.hidden{display:none!important}.site-footer{margin-top:5rem;border-top:1px solid var(--ink);padding:2.5rem 0 3.5rem}.footer-inner{display:flex;justify-content:space-between;gap:2rem;align-items:end}.footer-brand{font:400 1.9rem var(--serif)}.footer-note{color:var(--muted);font-size:.84rem;margin-top:.35rem}.footer-links{display:flex;gap:1rem;flex-wrap:wrap;font-size:.82rem;color:var(--muted)}
@media(max-width:900px){.header-inner,.secondary-page{padding-left:1rem;padding-right:1rem}.nav{display:none;position:absolute;left:0;right:0;top:100%;background:var(--paper);border-bottom:1px solid var(--line);padding:1rem;flex-direction:column;align-items:flex-start;gap:.7rem}.nav.open{display:flex}.menu{display:block;margin-left:auto}.presence{display:none}.page-hero{display:block;padding:3.4rem 0 1.7rem}.page-hero p{font-size:1rem}.wrap{padding-top:1.7rem}.section-head{display:block;padding-top:1.8rem}.section-head>p{margin-top:.65rem}.toolbar{display:grid;grid-template-columns:1fr;flex-wrap:wrap}.toolbar .more-filters .more-filters-row{position:static;width:100%;box-shadow:none;min-width:0}.control{width:100%}.event{grid-template-columns:1fr;gap:.4rem;padding:1.35rem 0 1.45rem}.event:before{left:0;top:1.72rem}.event:after{left:4px;top:2.35rem}.time,.date-note{padding-left:1.25rem;display:inline-block}.date-note{margin-left:.4rem}.meta-line{display:block}.meta-label{display:block;margin-bottom:.2rem}.journal-table{display:block;overflow-x:auto;max-width:100%}.footer-inner{align-items:flex-start;flex-direction:column}}
.detail-page{max-width:920px;margin:0 auto}.detail-kicker{font:600 .72rem var(--mono);letter-spacing:.08em;color:var(--blue);margin:0 0 1rem;text-transform:uppercase}.detail-page h1{font:400 clamp(2rem,5vw,4rem)/1.08 var(--serif);margin:0;overflow-wrap:anywhere}.detail-title-secondary{font:400 1.08rem/1.55 var(--serif);color:var(--muted);margin:.85rem 0 0;overflow-wrap:anywhere}.detail-authors{color:var(--muted);margin:1rem 0 1.5rem;overflow-wrap:anywhere}.detail-links{display:flex;gap:.6rem;flex-wrap:wrap;margin:1.25rem 0 1.8rem}.detail-links a{border:1px solid var(--line);background:var(--white);padding:.55rem .75rem;overflow-wrap:anywhere}.detail-links a.primary{background:var(--blue);border-color:var(--blue);color:#fff}.detail-meta{display:grid;grid-template-columns:140px minmax(0,1fr);border-top:1px solid var(--ink);margin:1.5rem 0 2.2rem}.detail-meta>div{padding:.75rem 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.detail-meta .label{font-weight:700}.detail-abstract{border-top:1px solid var(--ink);padding-top:1rem;margin-top:2rem}.detail-abstract h2{font:400 1.55rem var(--serif);margin:0 0 .65rem}.detail-abstract p{white-space:pre-line;line-height:1.8}.detail-loading{min-height:280px}.related-list{display:grid;gap:.55rem;padding-left:1.2rem}
@media(max-width:900px){.detail-meta{grid-template-columns:1fr}.detail-meta .label{padding-bottom:0;border-bottom:0}.detail-meta .label+div{padding-top:.25rem}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important}}
"""


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " "))


def topic_label(topic: str) -> str:
    return TOPIC_LABELS.get(topic, topic.replace("_", " "))


def ordered_topic_counts(records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts = Counter(topic for record in records for topic in article_topics(record))
    items = list(counts.items())
    return sorted(items, key=lambda item: (0 if item[0] == "china" else 1, -item[1], topic_label(item[0])))


def normalize_attr(value: Any) -> str:
    return str(value or "").lower().replace('"', "&quot;")


def record_url(record: dict[str, Any]) -> str:
    return record.get("url") or (f"https://doi.org/{record['doi']}" if record.get("doi") else "#")


def authors(record: dict[str, Any], limit: int = 5) -> str:
    names = record.get("authors") or []
    return ", ".join(names[:limit]) + (" 等" if len(names) > limit else "")


def beijing_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(CN_TZ)


def beijing_date(value: str | None) -> str:
    dt = beijing_datetime(value)
    return dt.date().isoformat() if dt else ""


def beijing_time(value: str | None) -> str:
    dt = beijing_datetime(value)
    return dt.strftime("%H:%M") if dt else ""


def beijing_stamp(value: str | None) -> str:
    dt = beijing_datetime(value)
    return dt.strftime("%Y-%m-%d %H:%M 北京时间") if dt else "暂无"


def latest_status_timestamp(status: dict[str, Any], records: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    workflow = status.get("workflow") or {}
    for key in (
        "finished_at",
        "updated_at",
        "last_light_finished_at",
        "last_full_finished_at",
        "last_single_finished_at",
    ):
        value = workflow.get(key)
        if value:
            candidates.append(str(value))
    for entry in workflow.get("history") or []:
        if isinstance(entry, dict) and entry.get("finished_at"):
            candidates.append(str(entry.get("finished_at")))
    for run in status.get("runs") or []:
        if isinstance(run, dict) and run.get("updated_at"):
            candidates.append(str(run.get("updated_at")))
    for item in (status.get("sources") or {}).values():
        if isinstance(item, dict) and item.get("updated_at"):
            candidates.append(str(item.get("updated_at")))
    for record in records:
        if record.get("detected_at"):
            candidates.append(str(record.get("detected_at")))
    valid = [value for value in candidates if beijing_datetime(value)]
    return max(valid, default="")


def monitor_freshness_label(value: str | None, status: dict[str, Any] | None = None) -> str:
    dt = beijing_datetime(value)
    if not dt:
        return "状态待确认"
    hours = (datetime.now(CN_TZ) - dt).total_seconds() / 3600
    if hours <= 2.25:
        return "状态正常"
    if hours <= 6:
        return "超过 2 小时未刷新"
    return "超过 6 小时未刷新"


def next_hourly_run(value: str | None) -> str:
    now = datetime.now(CN_TZ)
    dt = max(beijing_datetime(value) or now, now)
    next_dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return next_dt.strftime("%Y-%m-%d %H:%M 北京时间")


def next_daily_full_run(value: str | None) -> str:
    now = datetime.now(CN_TZ)
    dt = max(beijing_datetime(value) or now, now)
    windows = [(2, 30), (8, 30), (14, 30), (20, 30)]
    candidates = [dt.replace(hour=hour, minute=minute, second=0, microsecond=0) for hour, minute in windows]
    future = [candidate for candidate in candidates if candidate > dt]
    candidate = future[0] if future else candidates[0] + timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M 北京时间")


def load_all_daily(daily_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not daily_dir.exists():
        return records
    daily_keys: set[str] = set()
    for path in sorted(daily_dir.glob("*.json"), reverse=True):
        for record in read_json(path, []):
            record["_daily_date"] = path.stem
            records.append(record)
            daily_keys.update(record_match_keys(record))
    seen = read_json(DATA_DIR / "seen.json", {"papers": {}})
    seen_papers = seen.get("papers") if isinstance(seen, dict) else {}
    if isinstance(seen_papers, dict):
        for record_id, record in seen_papers.items():
            if not isinstance(record, dict):
                continue
            if record_match_keys(record) & daily_keys:
                continue
            first_seen = beijing_date(record.get("first_seen")) or str(record.get("first_seen") or "")[:10]
            if not first_seen:
                continue
            restored = dict(record)
            restored["id"] = restored.get("id") or record_id
            restored["_daily_date"] = first_seen
            restored["_from_seen_only"] = True
            records.append(restored)
            daily_keys.update(record_match_keys(restored))
    return sort_records(records)


def detected_date(record: dict[str, Any]) -> str:
    return str(record.get("_daily_date") or "") or beijing_date(record.get("detected_at")) or ""


def record_is_on_date(record: dict[str, Any], target_date: str) -> bool:
    """A paper belongs to a day's view when it was found or officially online that day."""
    # seen-only records are restored for catalogue/search pages. Their
    # first_seen date must not recreate a historical item in today's flow.
    if record.get("_from_seen_only") and detected_date(record) == target_date:
        return target_date in verified_online_dates(record)
    return detected_date(record) == target_date or target_date in verified_online_dates(record)


def verified_online_dates(record: dict[str, Any]) -> set[str]:
    """Return online dates strong enough to drive public date views.

    Crossref/OpenAlex/Unpaywall dates are useful metadata evidence, but they
    are not proof that the publisher made the article available online. They
    must not resurrect an old record into today's first-discovery stream.
    """
    source = str(record.get("date_source") or "").casefold()
    confidence = str(record.get("date_confidence") or "").upper()
    weak_provider = any(token in source for token in ("crossref", "openalex", "unpaywall"))
    if weak_provider or confidence in {"C", "D", "F", "UNKNOWN", ""}:
        return set()
    return {
        str(record.get("available_online") or "").strip(),
        str(record.get("published_online") or "").strip(),
    } - {""}


def detected_time(record: dict[str, Any]) -> str:
    return beijing_time(record.get("detected_at"))


def detected_label(record: dict[str, Any]) -> str:
    time_text = detected_time(record)
    return f"本站首次发现 {detected_date(record)}{f' {time_text}' if time_text else ''}"


def sortable_official_date(record: dict[str, Any]) -> str:
    return str(
        record.get("available_online")
        or record.get("published_online")
        or record.get("issue_date")
        or ""
    )


def sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest daily buckets first; metadata backfills must not reorder days."""
    ordered = sorted(records, key=lambda item: str(item.get("title") or "").casefold())
    ordered = sorted(ordered, key=sortable_official_date, reverse=True)
    ordered = sorted(ordered, key=lambda item: str(item.get("detected_at") or ""), reverse=True)
    return sorted(ordered, key=detected_date, reverse=True)


def is_china_related(record: dict[str, Any]) -> bool:
    if record.get("china_related") is False and str(record.get("china_related_source") or "") == "manual":
        return False
    title_text = " ".join(str(record.get(key) or "") for key in ("title", "title_zh")).casefold()
    has_title_signal = any(re.search(pattern, title_text, flags=re.I) for pattern in CHINA_TITLE_PATTERNS)
    return (
        record.get("china_related") is True
        or record.get("china_relevance_status") == "confirmed"
        or "china" in {str(field) for field in record.get("fields", []) or []}
        or has_title_signal
    )


def has_public_title(record: dict[str, Any]) -> bool:
    title = str(record.get("title") or "").strip()
    lowered = title.casefold()
    abstract_starts = (
        "this paper ",
        "this study ",
        "we analyze ",
        "we analyse ",
        "we examine ",
        "we investigate ",
        "using data ",
        "based on ",
    )
    if not title or "题名待解析" in title or lowered.startswith("untitled"):
        return False
    if "repec nep" in lowered and " item p" in lowered:
        return False
    if len(title) > 260 or any(lowered.startswith(prefix) for prefix in abstract_starts):
        return False
    if record.get("public_visible") is False or record.get("title_parse_status") == "needs_repec_detail_title":
        return False
    return True


def is_public_china_related(record: dict[str, Any]) -> bool:
    return is_china_related(record) and has_public_title(record)


def search_catalog_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate public search records without dropping duplicate China evidence."""
    public = public_records(records)
    china_keys = {display_key(record) for record in public if is_public_china_related(record)}
    projected: list[dict[str, Any]] = []
    for record in unique_records(public):
        if display_key(record) in china_keys and not is_china_related(record):
            record = dict(record)
            record["china_related"] = True
            record["china_related_source"] = "duplicate_projection"
        projected.append(record)
    return projected


def is_working_paper(record: dict[str, Any]) -> bool:
    source_type = str(record.get("source_type") or "")
    return str(record.get("source") or "") == "working_papers" or source_type in {"working_paper", "policy_paper", "aggregator"}


def is_today_home_flow_record(record: dict[str, Any]) -> bool:
    """Keep the homepage focused on fresh signals, not stale catalogue backfill."""
    if not record_is_on_date(record, today_str()):
        return False
    if (
        str(record.get("source") or "") == "cn-official"
        and str(record.get("date_source") or "") == "issue_only"
        and str(record.get("date_confidence") or "") in {"D", "F", "unknown", ""}
    ):
        return False
    if detected_date(record) == today_str():
        try:
            cutoff = date.fromisoformat(today_str()) - timedelta(days=2)
            verified = [
                date.fromisoformat(value[:10])
                for value in verified_online_dates(record)
                if value
            ]
        except ValueError:
            verified = []
        if verified and all(value < cutoff for value in verified):
            return False
    return True


def display_key(record: dict[str, Any]) -> str:
    for key in ("doi",):
        value = record.get(key)
        if value:
            return f"{key}:{str(value).casefold()}"
    title = " ".join(str(record.get("title") or "").casefold().split())
    if title:
        return f"title:{title}"
    for key in ("id", "url"):
        value = record.get(key)
        if value:
            return f"{key}:{str(value).casefold()}"
    return "title:"


def detail_key(record: dict[str, Any]) -> str:
    """Return the stable public key used by ``docs/paper.html``."""
    canonical_key = str(record.get("canonical_detail_key") or record.get("detail_key") or "").strip()
    if re.search(r"-[0-9a-f]{12}$", canonical_key, re.IGNORECASE):
        return canonical_key
    title = str(record.get("title") or "paper").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-") or "paper"
    slug = slug[:88].rstrip("-")
    identity = normalize_doi(record.get("doi")) or str(record.get("url") or "")
    if not identity:
        identity = f"{record.get('title') or ''}|{record.get('journal') or ''}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def detail_url(record: dict[str, Any]) -> str:
    return f"{BASE}/paper/{detail_key(record)}/"


def unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        key = display_key(record)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def source_type_label(record: dict[str, Any]) -> str:
    source_type = str(record.get("source_type") or "")
    return {
        "working_paper": "工作论文",
        "policy_paper": "机构研究",
        "policy_commentary": "研究评论",
        "aggregator": "聚合源",
        "journal": "期刊论文",
        "journal_article": "期刊论文",
    }.get(source_type, "工作论文" if is_working_paper(record) else "期刊论文")


def source_type_value(record: dict[str, Any]) -> str:
    source_type = str(record.get("source_type") or ("working_paper" if is_working_paper(record) else "journal_article"))
    return "journal_article" if source_type in {"journal", "journal_article"} else source_type


def article_topics(record: dict[str, Any]) -> list[str]:
    fields = [str(field).casefold() for field in record.get("fields", []) or []]
    topics: list[str] = []
    if is_china_related(record) or ("china" in fields and not is_working_paper(record)):
        topics.append("china")
    topics.extend(article_topic_codes(record, limit=4))
    if not topics and not is_working_paper(record):
        topics.append("development")
    return list(dict.fromkeys(topics))[:4]


def working_paper_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sort_records(unique_records([record for record in records if is_working_paper(record) and has_public_title(record)]))


def public_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sort_records([record for record in records if has_public_title(record)])


def journal_lookup() -> dict[str, dict[str, Any]]:
    lookup = {journal["id"]: journal for journal in load_journals(DATA_DIR / "journals.yml")}
    for source in load_working_paper_sources():
        source_id = str(source.get("id") or "")
        if not source_id:
            continue
        lookup[f"source-{source_id}"] = {
            "id": f"source-{source_id}",
            "title": source.get("title") or source_id,
            "chinese_name": SOURCE_CN_NAMES.get(source_id) or source.get("chinese_name") or "",
        }
    return lookup


SOURCE_STATUS = {
    "iza": ("已跑通", "ok", "RSS/页面入口可抓取，已纳入第一批测试。"),
    "cepr-dp": ("已跑通", "ok", "公开页面可抓取，已纳入第二批测试。"),
    "fed-feds": ("已跑通", "ok", "公开页面可抓取，已纳入第二批测试。"),
    "nber": ("已增强", "ok", "已接入 NBER 列表 API 和论文详情页。"),
    "world-bank-prwp": ("已增强", "ok", "已接入 World Bank Open Knowledge 详情 API，用详情页校验标题、摘要和日期。"),
    "imf-working-papers": ("替代源已接入", "ok", "官方页面访问不稳定，当前使用 IDEAS/RePEc 的 IMF Working Papers 公开系列页。"),
    "bis-working-papers": ("已跑通", "ok", "已接入 BIS 官方 RSS，并过滤 Working Papers。"),
    "cesifo-working-papers": ("已跑通", "ok", "已接入 IDEAS/RePEc 的 CESifo Working Papers 系列页。"),
    "oecd-working-papers": ("替代源已接入", "ok", "OECD/iLibrary 页面访问不稳定，当前使用 IDEAS/RePEc 的 OECD Economics Department Working Papers 公开系列页。"),
    "repec-nep-cna": ("已接入", "ok", "RePEc NEP 中国经济学细分类，优先补充与中国相关工作论文发现。"),
    "repec-nep-dev": ("已接入", "ok", "RePEc NEP 发展经济学细分类，用于补充机构工作论文源。"),
    "repec-nep-hea": ("已接入", "ok", "RePEc NEP 健康经济学细分类，作为 SSRN Health Economics 的公开替代入口之一。"),
    "repec-nep-mac": ("已接入", "ok", "RePEc NEP 宏观经济学细分类，用于补充 RePEc 新稿。"),
    "repec-nep-ifn": ("已接入", "ok", "RePEc NEP 国际金融细分类，用于补充 IMF/BIS/OECD 之外的宏观金融工作论文。"),
    "voxeu-cepr-columns": ("试运行", "ok", "已接入 CEPR 官方 Vox 内容 RSS；作为研究评论单独展示，不混入工作论文主列表。"),
    "brookings-economic-studies": ("试运行", "ok", "已接入 Brookings Economic Studies 官方栏目页；文章日期和内容类型继续校验。"),
    "iza-newsroom": ("受限暂缓", "pause", "官方站点当前返回登录页，暂不使用非官方转载源；正式论文仍以 IZA Discussion Papers 为准。"),
    "repec-nep": ("暂缓", "pause", "聚合源噪声较高，先放到第三阶段。"),
    "ssrn-economics-research-network": ("受限待接邮件/feed", "pause", "SSRN 公开页面常返回访问限制；后续优先接邮件订阅或具体 eJournal feed。"),
    "ssrn-health-economics-network": ("受限待接邮件/feed", "pause", "SSRN 公开页面常返回访问限制；后续优先接邮件订阅或具体 eJournal feed。"),
}


SOURCE_TYPE_LABELS = {
    "working_paper": "工作论文",
    "policy_paper": "机构研究",
    "aggregator": "聚合源",
    "policy_commentary": "研究评论",
}


SOURCE_CN_NAMES = {
    "nber": "美国国家经济研究局工作论文",
    "iza": "IZA 讨论论文",
    "world-bank-prwp": "世界银行政策研究工作论文",
    "imf-working-papers": "IMF 工作论文",
    "repec-nep": "RePEc NEP 新经济学论文",
    "ssrn-economics-research-network": "SSRN 经济学研究网络",
    "ssrn-health-economics-network": "SSRN 健康经济学网络",
    "cepr-dp": "CEPR 讨论论文",
    "cesifo-working-papers": "CESifo 工作论文",
    "fed-feds": "美联储 FEDS 工作论文",
    "bis-working-papers": "国际清算银行工作论文",
    "oecd-working-papers": "OECD 工作论文",
    "repec-nep-cna": "RePEc NEP 中国经济学论文",
    "repec-nep-dev": "RePEc NEP 发展经济学论文",
    "repec-nep-hea": "RePEc NEP 健康经济学论文",
    "repec-nep-mac": "RePEc NEP 宏观经济学论文",
    "repec-nep-ifn": "RePEc NEP 国际金融论文",
    "voxeu-cepr-columns": "VoxEU / CEPR 专栏",
    "brookings-economic-studies": "Brookings 经济研究",
    "iza-newsroom": "IZA 新闻室",
}


SOURCE_STATUS.update(
    {
        "iza": ("已跑通", "ok", "公开页面可抓取，已纳入第一批来源。"),
        "cepr-dp": ("已跑通", "ok", "公开页面可抓取，已纳入第二批来源。"),
        "fed-feds": ("已跑通", "ok", "公开页面可抓取，继续补强标题和日期解析。"),
        "nber": ("已增强", "ok", "已接入 NBER 列表 API 和论文详情页。"),
        "world-bank-prwp": ("已增强", "ok", "已接入 World Bank Open Knowledge 详情 API。"),
        "imf-working-papers": ("替代源已接入", "ok", "官方页面访问不稳定，当前使用 IDEAS/RePEc 的 IMF Working Papers 系列页。"),
        "bis-working-papers": ("已跑通", "ok", "已接入 BIS 官方 RSS，并过滤 Working Papers。"),
        "cesifo-working-papers": ("已跑通", "ok", "已接入 IDEAS/RePEc 的 CESifo Working Papers 系列页。"),
        "oecd-working-papers": ("替代源已接入", "ok", "OECD/iLibrary 页面访问不稳定，当前使用 IDEAS/RePEc 公开系列页。"),
        "repec-nep-cna": ("已接入", "ok", "RePEc NEP 中国经济学细分类，优先补充中国相关工作论文。"),
        "repec-nep-dev": ("已接入", "ok", "RePEc NEP 发展经济学细分类，用于补充机构工作论文源。"),
        "repec-nep-hea": ("已接入", "ok", "RePEc NEP 健康经济学细分类，作为 SSRN Health Economics 的公开替代入口之一。"),
        "repec-nep-mac": ("已接入", "ok", "RePEc NEP 宏观经济学细分类，用于补充 RePEc 新稿。"),
        "repec-nep-ifn": ("已接入", "ok", "RePEc NEP 国际金融细分类，用于补充宏观金融工作论文。"),
        "voxeu-cepr-columns": ("试运行", "ok", "已接入 CEPR 官方 Vox 内容 RSS，作为研究评论单独展示。"),
        "brookings-economic-studies": ("试运行", "ok", "已接入 Brookings Economic Studies 官方栏目页，继续校验日期字段。"),
        "iza-newsroom": ("受限暂缓", "pause", "官方站点当前返回登录页，暂不使用非官方转载源。"),
        "repec-nep": ("暂缓", "pause", "全量聚合源噪声较高，先放到第三阶段。"),
        "ssrn-economics-research-network": ("受限，暂缓", "pause", "SSRN 公开页面常返回访问限制；后续优先接邮件订阅或具体 eJournal feed。"),
        "ssrn-health-economics-network": ("受限，暂缓", "pause", "SSRN 公开页面常返回访问限制；后续优先接邮件订阅或具体 eJournal feed。"),
    }
)

SOURCE_TYPE_LABELS.update(
    {
        "working_paper": "工作论文",
        "policy_paper": "机构研究",
        "aggregator": "聚合源",
        "policy_commentary": "研究评论",
    }
)

SOURCE_CN_NAMES.update(
    {
        "nber": "美国国家经济研究局工作论文",
        "iza": "IZA 讨论论文",
        "world-bank-prwp": "世界银行政策研究工作论文",
        "imf-working-papers": "IMF 工作论文",
        "repec-nep": "RePEc NEP 新经济学论文",
        "ssrn-economics-research-network": "SSRN 经济学研究网络",
        "ssrn-health-economics-network": "SSRN 健康经济学网络",
        "cepr-dp": "CEPR 讨论论文",
        "cesifo-working-papers": "CESifo 工作论文",
        "fed-feds": "美联储 FEDS 工作论文",
        "bis-working-papers": "国际清算银行工作论文",
        "oecd-working-papers": "OECD 工作论文",
        "repec-nep-cna": "RePEc NEP 中国经济学论文",
        "repec-nep-dev": "RePEc NEP 发展经济学论文",
        "repec-nep-hea": "RePEc NEP 健康经济学论文",
        "repec-nep-mac": "RePEc NEP 宏观经济学论文",
        "repec-nep-ifn": "RePEc NEP 国际金融论文",
        "voxeu-cepr-columns": "VoxEU / CEPR 专栏",
        "brookings-economic-studies": "Brookings 经济研究",
        "iza-newsroom": "IZA 新闻室",
    }
)


def load_working_paper_sources(path: Path = DATA_DIR / "working_paper_sources.yml") -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        import yaml

        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        sources = loaded.get("sources") or []
        return [source for source in sources if isinstance(source, dict)]
    except Exception:
        sources: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- id:"):
                if current:
                    sources.append(current)
                current = {"id": stripped.split(":", 1)[1].strip()}
            elif current is not None and ":" in stripped and not stripped.startswith("- "):
                key, value = stripped.split(":", 1)
                value = value.strip().strip('"').strip("'")
                if key.strip() == "stage":
                    try:
                        current[key.strip()] = int(value)
                    except ValueError:
                        current[key.strip()] = value
                else:
                    current[key.strip()] = value
        if current:
            sources.append(current)
        return sources


def working_paper_sources_body(records: list[dict[str, Any]]) -> str:
    sources = load_working_paper_sources()
    status = load_status()
    source_statuses = status.get("sources") or {}
    wp_records = working_paper_records(records)
    by_source = Counter(str(record.get("source_id") or "").removeprefix("source-") for record in wp_records)
    today_by_source = Counter(str(record.get("source_id") or "").removeprefix("source-") for record in wp_records if record_is_on_date(record, today_str()))
    rows = []
    stable = partial = failed = 0

    def public_source_group(source: dict[str, Any]) -> str:
        try:
            stage = int(source.get("stage") or 99)
        except (TypeError, ValueError):
            stage = 99
        if stage <= 1:
            return "核心"
        if stage == 2:
            return "扩展"
        return "候选"

    def public_source_note(source_id: str, status_class: str, default_note: str) -> str:
        if status_class == "ok":
            return "已纳入日常监测。"
        if source_id.startswith("ssrn-"):
            return "暂未纳入公开监测，等待稳定 feed 或邮件来源。"
        if status_class == "pause":
            return "暂未纳入公开监测，等待稳定公开入口。"
        return "继续校验公开入口和日期字段。"

    for source in sources:
        source_id = str(source.get("id") or "")
        run_status = source_statuses.get(f"working-paper:{source_id}") or {}
        configured_label, configured_class, note = SOURCE_STATUS.get(source_id, ("待评估", "todo", "已加入配置，等待抓取验证。"))
        recent_count = int(run_status.get("count") or 0)
        total_count = by_source.get(source_id, 0)
        if run_status and not run_status.get("ok"):
            label, status_class = "暂未收录", "pause"
            failed += 1
        elif recent_count > 0 or total_count > 0:
            label, status_class = "稳定出数据", "ok"
            stable += 1
        elif configured_class == "pause":
            label, status_class = "暂未收录", configured_class
            failed += 1
        else:
            label, status_class = "继续校验", "todo"
            partial += 1
        chinese_name = SOURCE_CN_NAMES.get(source_id) or str(source.get("chinese_name") or "")
        homepage = str(source.get("homepage") or "")
        homepage_html = f'<a href="{html_escape(homepage)}">{html_escape(homepage)}</a>' if homepage else '<span class="muted">未配置</span>'
        public_note = public_source_note(source_id, status_class, note)
        rows.append(
            f"""<tr>
  <td><strong>{html_escape(str(source.get("title") or source_id))}</strong><div class="muted">{html_escape(chinese_name)}</div></td>
  <td>{html_escape(SOURCE_TYPE_LABELS.get(str(source.get("type") or ""), str(source.get("type") or "")))}</td>
  <td>{html_escape(public_source_group(source))}</td>
  <td><span class="source-status {html_escape(status_class)}">{html_escape(label)}</span><div class="muted">{html_escape(public_note)}</div></td>
  <td>{recent_count}</td>
  <td>{today_by_source.get(source_id, 0)}</td>
  <td>{total_count}</td>
  <td>{html_escape(beijing_stamp(run_status.get("updated_at"))) if run_status else '<span class="muted">暂无</span>'}</td>
  <td>{homepage_html}</td>
</tr>"""
        )
    total_today = sum(today_by_source.values())
    total_records = len(wp_records)
    return f"""<section class="section-head">
  <div><h2>工作论文来源</h2><p>覆盖 NBER、IZA、World Bank、IMF、CEPR、BIS、CESifo、OECD 与 RePEc NEP 等公开元数据来源。</p></div>
  <p>{len(sources)} 个来源</p>
</section>
<section class="stats">
  <a class="stat" href="{BASE}/working-papers/"><strong>{total_records}</strong><span>累计工作论文记录</span></a>
  <a class="stat" href="{BASE}/working-papers/today/"><strong>{total_today}</strong><span>今日新发现</span></a>
  <span class="stat"><strong>{stable}</strong><span>稳定来源</span></span>
  <span class="stat"><strong>{partial}</strong><span>继续校验来源</span></span>
  <span class="stat"><strong>{failed}</strong><span>暂未收录来源</span></span>
</section>
<table class="journal-table"><thead><tr><th>来源</th><th>类型</th><th>分组</th><th>状态</th><th>本轮</th><th>今日</th><th>累计</th><th>最近更新</th><th>入口</th></tr></thead><tbody>{"".join(rows)}</tbody></table>"""


def stats(records: list[dict[str, Any]], today_records: list[dict[str, Any]], flow_records: list[dict[str, Any]]) -> dict[str, Any]:
    today = today_str()
    today_journals = {record.get("journal_id") for record in today_records if record.get("journal_id")}
    all_journals = {record.get("journal_id") for record in records if record.get("journal_id")}
    status = load_status()
    workflow = status.get("workflow") or {}
    last_record_seen = max((record.get("detected_at") or "" for record in records), default="")
    last_run = latest_status_timestamp(status, records) or last_record_seen
    return {
        "today": len(today_records),
        "china_today": sum(1 for record in today_records if is_china_related(record)),
        "online_today": sum(1 for record in today_records if today in {record.get("available_online"), record.get("published_online")}),
        "today_journals": len(today_journals),
        "flow": len(flow_records),
        "china_flow": sum(1 for record in flow_records if is_china_related(record)),
        "online_today_flow": sum(1 for record in flow_records if today in {record.get("available_online"), record.get("published_online")}),
        "flow_journals": len({record.get("journal_id") for record in flow_records if record.get("journal_id")}),
        "all_records": len(records),
        "all_journals": len(all_journals),
        "last_run": beijing_stamp(last_run),
        "last_run_freshness": monitor_freshness_label(last_run, status),
        "last_run_label": workflow.get("mode_label") or "自动监测",
        "last_full_run": beijing_stamp(workflow.get("last_full_finished_at")),
        "last_light_run": beijing_stamp(workflow.get("last_light_finished_at")),
        "next_light_run": next_hourly_run(workflow.get("last_light_finished_at") or last_run),
        "next_full_run": next_daily_full_run(workflow.get("last_full_finished_at") or last_run),
        "last_record_seen": beijing_stamp(last_record_seen),
    }


def date_type(record: dict[str, Any]) -> str:
    if record.get("available_online"):
        return "available_online"
    if record.get("published_online"):
        return "published_online"
    if record.get("accepted_date"):
        return "accepted"
    if record.get("source_issue") or record.get("issue_date"):
        return "issue"
    return "first_seen"


def date_type_label(value: str) -> str:
    return {
        "accepted": "接受日期",
        "available_online": "官方在线",
        "published_online": "官方发布",
        "issue": "来源期次",
        "first_seen": "本站首次发现",
    }.get(value, value)


def confidence_value(record: dict[str, Any]) -> str:
    if record.get("date_confidence"):
        return str(record.get("date_confidence"))
    return {"accepted": "A", "available_online": "A", "published_online": "B", "issue": "D", "first_seen": "F"}.get(date_type(record), "F")


def confidence_label(value: str) -> str:
    return {
        "A": "A：官方渠道明确日期",
        "B": "B：官方渠道备选/推断日期",
        "C": "C：Crossref/聚合登记日期",
        "D": "D：卷期/印刷日期",
        "F": "F：仅本站首次发现",
    }.get(value, value)


def public_date_label(record: dict[str, Any]) -> str:
    date_source = str(record.get("date_source") or "").casefold()
    if date_source.startswith("cnki_rss"):
        return "官方发布"
    if "crossref" in date_source:
        return "Crossref 元数据日期"
    if "openalex" in date_source or "unpaywall" in date_source:
        return "聚合元数据日期"
    if record.get("date_precision") == "month" and (record.get("available_online") or record.get("published_online")):
        return "官方在线月份"
    if record.get("available_online"):
        return "官方在线"
    if record.get("published_online"):
        return "官方发布"
    if record.get("accepted_date"):
        return "接受日期"
    if record.get("source_issue"):
        return "来源期次"
    if record.get("issue_date"):
        return "卷期日期"
    return "官方日期待补"


def official_date(record: dict[str, Any]) -> str:
    """Return a date that can support an online/publication claim.

    Acceptance is an editorial-process date, not evidence that the paper was
    published online. ``source_issue`` is a label rather than a date and is
    therefore kept out of this machine-comparable value as well.
    """
    return str(record.get("available_online") or record.get("published_online") or record.get("issue_date") or "")


def display_date(record: dict[str, Any]) -> str:
    """Return the best date to display while preserving its label."""
    return str(
        official_date(record)
        or record.get("accepted_date")
        or record.get("source_issue")
        or ""
    )


def date_source_label(record: dict[str, Any]) -> str:
    source = str(record.get("source") or "").casefold()
    date_source = str(record.get("date_source") or "").casefold()
    source_url = str(record.get("source_url") or "").casefold()
    if "pdf" in date_source:
        return "PDF"
    if date_source == "tandf_issue_date_fallback":
        return "T&F 备选日期"
    if date_source == "cepr_published_time":
        return "CEPR 页面"
    if source == "cnki-rss" or date_source.startswith("cnki_rss"):
        return "CNKI RSS"
    if "publisher" in date_source or "detail" in date_source:
        return "出版社网页"
    if "rss" in source or "rss" in date_source:
        return "RSS"
    if "crossref" in source or "crossref" in date_source or "crossref" in source_url:
        return "Crossref"
    if source in {"cn", "cn-journal", "official-source"} or record.get("source_issue"):
        return "期刊官网"
    if record.get("url"):
        return "文章页面"
    return "待补"


def public_date_line(record: dict[str, Any]) -> str:
    date_value = display_date(record)
    if record.get("date_precision") == "month" and date_value and date_value.count("-") == 2:
        date_value = date_value[:7]
    if date_value in {"待解析", "寰呰В鏋?", ""}:
        return f"官方日期待补 · 来源：{date_source_label(record)}"
    return f"{public_date_label(record)} {date_value} · 来源：{date_source_label(record)}"


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.count("-") != 2:
        return None
    try:
        return datetime.fromisoformat(text[:10])
    except ValueError:
        return None


def detection_lag_days(record: dict[str, Any]) -> int | None:
    online_dates = verified_online_dates(record)
    official = parse_date(next(iter(online_dates), ""))
    detected = parse_date(detected_date(record))
    if not official or not detected:
        return None
    return (detected.date() - official.date()).days


def detection_lag_chip(record: dict[str, Any]) -> str:
    if record.get("date_precision") in {"month", "year"}:
        return ""
    if str(record.get("date_confidence") or "").upper() == "F":
        return ""
    lag = detection_lag_days(record)
    if lag is None:
        return ""
    if lag <= 2:
        return ""
    if lag <= 30:
        return f'<span class="pill lag">滞后 {lag} 天</span>'
    return '<span class="pill lag">历史补录</span>'


def publisher_family(record: dict[str, Any]) -> str | None:
    text = " ".join(
        str(value or "")
        for value in [
            record.get("doi"),
            record.get("url"),
            record.get("source_url"),
            record.get("journal"),
            record.get("publisher"),
        ]
    ).casefold()
    if "10.1016/" in text or "sciencedirect.com" in text or "elsevier" in text:
        return "Elsevier / ScienceDirect"
    if "10.1080/" in text or "tandfonline.com" in text or "taylor" in text:
        return "Taylor & Francis"
    if "10.1111/" in text or "onlinelibrary.wiley.com" in text or "wiley" in text:
        return "Wiley"
    if "10.1093/" in text or "academic.oup.com" in text or "oxford university press" in text:
        return "OUP"
    return None


def source_delay_rows(records: list[dict[str, Any]], days: int = 14) -> str:
    recent = recent_detected_records(public_records(records), days)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in recent:
        family = publisher_family(record)
        if family:
            grouped[family].append(record)
    rows = []
    for family in ["Elsevier / ScienceDirect", "Taylor & Francis", "Wiley", "OUP"]:
        items = grouped.get(family, [])
        lags = [lag for record in items if (lag := detection_lag_days(record)) is not None]
        precise = [record for record in items if parse_date(str(record.get("available_online") or record.get("published_online") or record.get("issue_date") or ""))]
        rss_count = sum(1 for record in items if "rss" in str(record.get("source") or "").casefold() or "rss" in str(record.get("date_source") or "").casefold())
        crossref_count = sum(1 for record in items if "crossref" in str(record.get("date_source") or "").casefold())
        detail_count = sum(1 for record in items if "publisher" in str(record.get("date_source") or "").casefold() or "detail" in str(record.get("date_source") or "").casefold())
        delayed_count = sum(1 for lag in lags if lag > 2)
        no_precise_count = len(items) - len(precise)
        avg_lag = "待判定" if not lags else f"{sum(lags) / len(lags):.1f} 天"
        max_lag = "待判定" if not lags else f"{max(lags)} 天"
        rows.append(
            f"<tr><td>{html_escape(family)}</td><td>{len(items)}</td><td>{len(precise)}</td><td>{no_precise_count}</td><td>{delayed_count}</td><td>{avg_lag}</td><td>{max_lag}</td><td>{rss_count}</td><td>{detail_count}</td><td>{crossref_count}</td></tr>"
        )
    return "".join(rows)


def archive_official_date_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return "暂无记录"
    dates = sorted(
        {
            str(record.get("available_online") or record.get("published_online") or record.get("issue_date") or "")
            for record in records
            if str(record.get("available_online") or record.get("published_online") or record.get("issue_date") or "").count("-") == 2
        }
    )
    if not dates:
        issues = sorted({str(record.get("source_issue") or "") for record in records if record.get("source_issue")})
        if issues:
            return "来源期次：" + ("、".join(issues[:3]) + (" 等" if len(issues) > 3 else ""))
        return "待解析"
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]} 至 {dates[-1]}"


def hourly_journal_count() -> int:
    path = DATA_DIR / "monitor_tiers.yml"
    if not path.exists():
        return 0
    count = 0
    in_hourly = False
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "hourly:":
            in_hourly = True
            continue
        if in_hourly and stripped and not stripped.startswith("- "):
            break
        if in_hourly and stripped.startswith("- "):
            count += 1
    return count


def working_source_stage_count(max_stage: int) -> int:
    count = 0
    for source in load_working_paper_sources():
        try:
            stage = int(source.get("stage") or 99)
        except (TypeError, ValueError):
            stage = 99
        if stage <= max_stage and str(source.get("status") or "active") != "paused":
            count += 1
    return count


def monitor_summary_cards(records: list[dict[str, Any]], today_records: list[dict[str, Any]] | None = None) -> str:
    status = load_status()
    workflow = status.get("workflow") or {}
    journals = load_journals(DATA_DIR / "journals.yml")
    today_total = len(today_records if today_records is not None else [record for record in records if record_is_on_date(record, today_str())])
    light_journals = hourly_journal_count()
    light_sources = working_source_stage_count(1)
    full_journals = len(journals)
    full_sources = working_source_stage_count(2)
    return f"""<section class="audit-grid">
  <div class="audit-card"><strong>快速监测</strong><span>最近运行：{html_escape(beijing_stamp(workflow.get('last_light_finished_at')))}</span></div>
  <div class="audit-card"><strong>{light_journals}</strong><span>快速监测期刊</span></div>
  <div class="audit-card"><strong>{light_sources}</strong><span>快速监测工作论文源</span></div>
  <div class="audit-card"><strong>全量监测</strong><span>最近运行：{html_escape(beijing_stamp(workflow.get('last_full_finished_at')))}</span></div>
  <div class="audit-card"><strong>{full_journals}</strong><span>全量监测期刊</span></div>
  <div class="audit-card"><strong>{full_sources}</strong><span>全量监测工作论文源</span></div>
  <div class="audit-card"><strong>{today_total}</strong><span>今日新发现记录</span></div>
</section>"""


def sidebar(
    records: list[dict[str, Any]],
    *,
    context_records: list[dict[str, Any]] | None = None,
    context_date: str | None = None,
) -> str:
    side_records = public_records(context_records if context_records is not None else records)
    journal_side_records = [record for record in side_records if not is_working_paper(record)]
    working_side_records = [record for record in side_records if is_working_paper(record)]
    journal_counts = Counter(record.get("journal_id") for record in journal_side_records if record.get("journal_id"))
    working_counts = Counter(record.get("journal_id") for record in working_side_records if record.get("journal_id"))
    journals_by_id = journal_lookup()
    topics = "".join(
        f'<a class="side-link" href="{BASE}/daily/{html_escape(context_date or today_str())}/?field={html_escape(topic)}"><span class="side-main"><strong>{html_escape(topic_label(topic))}</strong></span><span class="count">{count}</span></a>'
        for topic, count in ordered_topic_counts(side_records)[:12]
    )
    journal_target_date = context_date or today_str()
    is_today_context = journal_target_date == today_str()
    topic_title = "今日文章主题" if is_today_context else f"{journal_target_date} 文章主题"
    journal_source_title = "今日期刊论文来源" if is_today_context else f"{journal_target_date} 期刊论文来源"
    working_source_title = "今日工作论文来源" if is_today_context else f"{journal_target_date} 工作论文来源"
    journal_footer_label = "查看今日期刊论文" if is_today_context else f"查看 {journal_target_date} 期刊论文"
    working_footer_label = "查看今日工作论文" if is_today_context else f"查看 {journal_target_date} 工作论文"
    working_footer_href = f"{BASE}/working-papers/today/" if is_today_context else f"{BASE}/daily/{html_escape(journal_target_date)}/?sourceType=working_paper"

    def source_links(counts: Counter, *, working: bool) -> str:
        links = []
        for journal_id, count in counts.most_common(10):
            journal = journals_by_id.get(journal_id, {})
            title = journal.get("title") or journal_id
            chinese_name = journal.get("chinese_name") or ""
            target = f"{BASE}/working-papers/today/?journal={html_escape(journal_id)}" if working else f"{BASE}/daily/{html_escape(journal_target_date)}/?journal={html_escape(journal_id)}"
            links.append(
                f'<a class="side-link" href="{target}"><span class="side-main"><strong>{html_escape(title)}</strong><em>{html_escape(chinese_name)}</em></span><span class="count">{count}</span></a>'
            )
        if not links:
            label = "工作论文来源" if working else "期刊更新"
            links.append(f'<div class="side-link"><span class="side-main"><strong>暂无{html_escape(label)}</strong></span><span class="count">0</span></div>')
        return "".join(links)

    journal_links = source_links(journal_counts, working=False)
    working_links = source_links(working_counts, working=True)
    return f"""<aside class="sidebar">
  <h1 class="brand">{SITE_NAME}</h1>
  <div class="subtitle">{SITE_SUBTITLE}</div>
  <div class="side-block"><div class="side-title">导航</div>
    <a class="side-link" href="{BASE}/"><span class="side-main"><strong>今日论文</strong></span><span class="count">今日</span></a>
    <a class="side-link" href="{BASE}/recent72/"><span class="side-main"><strong>最近72小时</strong></span><span class="count">近3天</span></a>
    <a class="side-link" href="{BASE}/topics/china/"><span class="side-main"><strong>与中国相关</strong></span><span class="count">主题</span></a>
    <a class="side-link" href="{BASE}/search/"><span class="side-main"><strong>全站检索</strong></span><span class="count">检索</span></a>
    <a class="side-link" href="{BASE}/journals/"><span class="side-main"><strong>监测期刊</strong></span><span class="count">清单</span></a>
    <a class="side-link" href="{BASE}/working-papers/"><span class="side-main"><strong>工作论文</strong></span><span class="count">论文</span></a>
    <a class="side-link" href="{BASE}/sources/working-papers/"><span class="side-main"><strong>工作论文来源</strong></span><span class="count">来源</span></a>
  </div>
  <div class="side-block"><div class="side-title">{html_escape(topic_title)}</div>{topics}</div>
  <div class="side-block"><div class="side-title">{html_escape(journal_source_title)}</div>{journal_links}<a class="side-link" href="{BASE}/daily/{html_escape(journal_target_date)}/"><span class="side-main"><strong>{html_escape(journal_footer_label)}</strong></span><span class="count">今日</span></a></div>
  <div class="side-block"><div class="side-title">{html_escape(working_source_title)}</div>{working_links}<a class="side-link" href="{working_footer_href}"><span class="side-main"><strong>{html_escape(working_footer_label)}</strong></span><span class="count">今日</span></a></div>
</aside>"""


def analytics_snippet() -> str:
    provider = os.environ.get("ANALYTICS_PROVIDER", "none").strip().lower()
    if provider in {"", "none", "off", "false"}:
        return ""
    if provider == "plausible":
        domain = os.environ.get("PLAUSIBLE_DOMAIN", "").strip()
        script_url = os.environ.get("PLAUSIBLE_SCRIPT_URL", "https://plausible.io/js/script.js").strip()
        if not domain:
            return ""
        return f'<script defer data-domain="{html_escape(domain)}" src="{html_escape(script_url)}"></script>'
    if provider == "umami":
        website_id = os.environ.get("UMAMI_WEBSITE_ID", "").strip()
        script_url = os.environ.get("UMAMI_SCRIPT_URL", "").strip()
        if not website_id or not script_url:
            return ""
        return f'<script defer src="{html_escape(script_url)}" data-website-id="{html_escape(website_id)}"></script>'
    if provider in {"google", "ga", "gtag"}:
        measurement_id = os.environ.get("GA_MEASUREMENT_ID", "").strip()
        if not measurement_id:
            return ""
        escaped_id = html_escape(measurement_id)
        return f"""<script async src="https://www.googletagmanager.com/gtag/js?id={escaped_id}"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date());
gtag('config', '{escaped_id}');
</script>"""
    return ""


def presence_snippet() -> str:
    endpoint = os.environ.get("PRESENCE_ENDPOINT", DEFAULT_PRESENCE_ENDPOINT).strip()
    if not endpoint:
        return ""
    endpoint_js = json.dumps(endpoint)
    return f"""<script>
(() => {{
  const endpoint = {endpoint_js};
  const key = 'epd_presence_client';
  const clientId = localStorage.getItem(key) || (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2));
  localStorage.setItem(key, clientId);
  const target = document.querySelector('[data-presence-count]');
  if (!target) return;
  const heartbeat = () => fetch(endpoint + (endpoint.includes('?') ? '&' : '?') + 'client_id=' + encodeURIComponent(clientId), {{headers: {{'Accept': 'application/json'}}}})
    .then(response => response.ok ? response.json() : null)
    .then(data => {{ if (data && Number.isFinite(Number(data.online))) target.innerHTML = '<span class=\"presence-dot\">●</span> ' + data.online + ' 人在线'; }})
    .catch(() => {{}});
  heartbeat();
  window.setInterval(heartbeat, 60000);
}})();
</script>"""


def secondary_context_nav(active: str = "") -> str:
    links = [
        ("recent72", "最近72小时", f"{BASE}/recent72/"),
        ("china", "中国研究", f"{BASE}/topics/china/"),
        ("journals", "期刊", f"{BASE}/journals/"),
        ("working-papers", "工作论文", f"{BASE}/working-papers/"),
        ("search", "搜索", f"{BASE}/search/"),
        ("feed", "RSS", f"{BASE}/feed.xml"),
    ]
    return "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, label, href in links
    )


def secondary_page_lede(title: str) -> str:
    if title == "最近72小时":
        return "连续浏览近三天本站首次发现到的经济学研究内容，适合快速补齐最近的发现流。"
    if title == "与中国相关":
        return "集中查看明确涉及中国数据、制度、市场或研究对象的期刊论文与工作论文。"
    if title == "全站检索":
        return "检索全部历史记录，并组合期刊、主题、日期类型、可信度和来源类型筛选。"
    if title in {"监测期刊", "历史归档", "全部工作论文"}:
        return "使用与 Daily Door 同源的数据和页面体系，保持清晰、可检索、可连续浏览。"
    if "归档" in title:
        return "按本站首次发现日期组织的每日记录，官方发布日期与本站首次发现日期分开显示。"
    if "工作论文" in title:
        return "覆盖工作论文与机构研究来源，按本站首次发现时间倒序排列。"
    if "最近 7 天" in title:
        return "按最近有记录的日期向前滚动，帮助连续追踪同一来源或主题。"
    return "从规范化 canonical 数据生成，保留论文来源、日期、主题和详情入口。"


def menu_script() -> str:
    return """<script>
(() => {
  const nav = document.querySelector('.nav');
  const menu = document.querySelector('.menu');
  if (!nav || !menu) return;
  menu.addEventListener('click', () => {
    const open = nav.classList.toggle('open');
    menu.setAttribute('aria-expanded', String(open));
    menu.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
  });
})();
</script>"""

LAZY_LIST_SCRIPT = r"""
<script>
(() => {
  const jsonCache = new Map();
  const loadJson = (url) => {
    const key = String(url);
    if (!jsonCache.has(key)) {
      jsonCache.set(key, fetch(url, {credentials: 'same-origin'}).then((response) => {
        if (!response.ok) throw new Error('lazy data request failed: ' + response.status);
        return response.json();
      }));
    }
    return jsonCache.get(key);
  };
  const debounce = (fn, delay) => {
    let timer;
    return () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(fn, delay);
    };
  };
  const queryTokens = (value) => {
    const text = String(value || '').trim().toLowerCase();
    const tokens = new Set();
    for (const word of text.match(/[a-z0-9]+/g) || []) {
      tokens.add(word);
      if (word.length >= 3) {
        for (let index = 0; index <= word.length - 3; index += 1) tokens.add(word.slice(index, index + 3));
      }
    }
    for (const run of text.match(/[\u3400-\u9fff]+/g) || []) {
      for (const char of run) tokens.add(char);
      for (let index = 0; index < run.length - 1; index += 1) tokens.add(run.slice(index, index + 2));
    }
    return [...tokens];
  };
  const routeTokens = (list, toolbar) => {
    const params = new URLSearchParams(window.location.search);
    const tokens = queryTokens(toolbar?.querySelector('[data-filter-role="search"]')?.value || '');
    const presetTokens = String(list?.dataset?.lazyFilter || '').trim().split(/\s+/).filter(Boolean);
    const journal = toolbar?.querySelector('[data-filter-role="journal"]')?.value || '';
    const field = toolbar?.querySelector('[data-filter-role="field"]')?.value || '';
    const dateType = toolbar?.querySelector('[data-filter-role="dateType"]')?.value || '';
    const confidence = toolbar?.querySelector('[data-filter-role="confidence"]')?.value || '';
    const sourceType = toolbar?.querySelector('[data-filter-role="sourceType"]')?.value || '';
    const china = toolbar?.querySelector('[data-filter-role="china"]')?.getAttribute('aria-pressed') === 'true';
    if (journal) tokens.push('journal:' + journal);
    if (field) tokens.push('field:' + field);
    if (dateType) tokens.push('date:' + dateType);
    if (confidence) tokens.push('confidence:' + confidence);
    if (sourceType) tokens.push('source:' + sourceType);
    if (china) tokens.push('china');
    if (params.get('onlineToday') === '1') tokens.push('online-today');
    tokens.push(...presetTokens);
    return [...new Set(tokens)];
  };
  const presetTokenMatches = (item, token) => {
    if (token.startsWith('journal:')) return item.journal === token.slice(8);
    if (token.startsWith('field:')) return String(item.fields || '').split(/\s+/).includes(token.slice(6));
    if (token.startsWith('source:')) return item.sourceType === token.slice(7);
    if (token === 'china') return Boolean(item.china);
    if (token === 'online-today') return Boolean(item.onlineToday);
    return String(item.search || '').includes(token);
  };
  const matchingItems = (items, toolbar, list) => {
    const query = toolbar ? (toolbar.querySelector('[data-filter-role="search"]')?.value || '').trim().toLowerCase() : '';
    const queryTokenList = query ? queryTokens(query) : [];
    const presetTokens = String(list?.dataset?.lazyFilter || '').trim().split(/\s+/).filter(Boolean);
    const journal = toolbar?.querySelector('[data-filter-role="journal"]')?.value || '';
    const field = toolbar?.querySelector('[data-filter-role="field"]')?.value || '';
    const dateType = toolbar?.querySelector('[data-filter-role="dateType"]')?.value || '';
    const confidence = toolbar?.querySelector('[data-filter-role="confidence"]')?.value || '';
    const sourceType = toolbar?.querySelector('[data-filter-role="sourceType"]')?.value || '';
    const chinaOnly = toolbar?.querySelector('[data-filter-role="china"]')?.getAttribute('aria-pressed') === 'true';
    const onlineTodayOnly = new URLSearchParams(window.location.search).get('onlineToday') === '1';
    return items.filter((item) => {
      if (!presetTokens.every((token) => presetTokenMatches(item, token))) return false;
      if (queryTokenList.length && !queryTokenList.every((token) => String(item.search || '').includes(token))) return false;
      if (journal && item.journal !== journal) return false;
      if (field && !String(item.fields || '').split(/\s+/).includes(field)) return false;
      if (dateType && item.dateType !== dateType) return false;
      if (confidence && item.confidence !== confidence) return false;
      if (sourceType && item.sourceType !== sourceType) return false;
      if (chinaOnly && !item.china) return false;
      if (onlineTodayOnly && !item.onlineToday) return false;
      return true;
    });
  };
  const clearRendered = (list) => {
    list.querySelectorAll('.lazy-initial, .lazy-paper, .lazy-more, .lazy-start').forEach((node) => node.remove());
  };
  const showError = (list, error) => {
    console.error(error);
    const empty = list.querySelector('[data-lazy-empty]');
    if (empty) {
      empty.hidden = false;
      empty.textContent = '检索内容暂时无法载入，请稍后重试。';
    }
  };
  const loadManifest = (state) => {
    if (!state.manifestPromise) {
      state.manifestPromise = loadJson(state.manifestUrl).then((manifest) => {
        state.manifest = manifest;
        return manifest;
      });
    }
    return state.manifestPromise;
  };
  const loadNextShard = async (state) => {
    const manifest = await loadManifest(state);
    if (state.nextShard >= manifest.shards.length) return false;
    const descriptor = manifest.shards[state.nextShard];
    state.nextShard += 1;
    const items = await loadJson(new URL('shards/' + descriptor.name + '.json', state.manifestUrl).href);
    const known = new Set(state.items.map((item) => item.key));
    state.items.push(...items.filter((item) => !known.has(item.key)));
    return true;
  };
  const ROUTE_BUCKETS = 256;
  const ROUTED_INITIAL_SHARDS = 2;
  const routeBucket = (token) => (token.charCodeAt(0) * 31 + (token.charCodeAt(1) || 0)) % ROUTE_BUCKETS;
  const loadRouted = async (state, tokens) => {
    const manifest = await loadManifest(state);
    if (manifest.routed === false) {
      state.items = [];
      state.pendingShards = [];
      while (await loadNextShard(state)) {
        // small datasets scan all shards locally; filters apply in render
      }
      state.routed = true;
      state.nextShard = 0;
      return;
    }
    const byBucket = new Map();
    for (const token of tokens) {
      const bucket = routeBucket(token);
      if (!byBucket.has(bucket)) byBucket.set(bucket, []);
      byBucket.get(bucket).push(token);
    }
    const routePayloads = (await Promise.all([...byBucket.entries()].map(async ([bucket, bucketTokens]) => {
      const payload = await loadJson(new URL('route/' + String(bucket).padStart(3, '0') + '.json', state.manifestUrl).href).catch(() => ({}));
      return bucketTokens.map((token) => (payload[token] || '').split(',').filter(Boolean));
    }))).flat();
    if (!routePayloads.length || routePayloads.some((payload) => !payload.length)) {
      state.items = [];
      state.pendingShards = [];
      state.routed = true;
      state.nextShard = 0;
      return;
    }
    const shardSets = routePayloads.map((payload) => new Set(payload));
    const smallest = [...shardSets].sort((left, right) => left.size - right.size)[0];
    const shardNames = [...smallest].filter((name) => shardSets.every((set) => set.has(name))).sort();
    state.pendingShards = shardNames.slice(ROUTED_INITIAL_SHARDS);
    const initialShards = shardNames.slice(0, ROUTED_INITIAL_SHARDS);
    const payloads = await Promise.all(initialShards.map((name) => loadJson(new URL('shards/' + name + '.json', state.manifestUrl).href)));
    state.items = payloads.flat();
    state.routed = true;
    state.nextShard = 0;
  };
  const loadNextRoutedShard = async (state) => {
    const name = state.pendingShards.shift();
    if (!name) return false;
    const items = await loadJson(new URL('shards/' + name + '.json', state.manifestUrl).href);
    const known = new Set(state.items.map((item) => item.key));
    state.items.push(...items.filter((item) => !known.has(item.key)));
    return true;
  };
  const escHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  const renderCard = (item, base) => {
    const card = item.card || {};
    const topics = (card.tp || []).map((label) => '<span class="pill">' + escHtml(label) + '</span>').join('');
    const link = card.d
      ? '<a class="doi" href="https://doi.org/' + escHtml(card.d) + '">' + escHtml(card.d) + '</a>'
      : (card.u ? '<a class="doi" href="' + escHtml(card.u) + '">文章链接</a>' : '');
    const missingDoi = card.d ? '' : '<span class="doi">暂无 DOI</span>';
    const typeTag = card.st ? '<span class="pill">' + escHtml(card.st) + '</span>' : '';
    const chinaTag = card.cn ? '<span class="pill china">与中国相关</span>' : '';
    const originalTitle = card.s ? '\n    <p class="title-original">' + escHtml(card.s) + '</p>' : '';
    const authorsHtml = card.a ? '\n    <p class="authors">' + escHtml(card.a) + '</p>' : '';
    const officialChip = '<span class="date-chip ' + escHtml(card.oc || '') + '">' + escHtml(card.od) + '</span>';
    const detectedChip = '<span class="pill">' + escHtml(card.dl || card.dd + (card.dt ? ' ' + card.dt : '')) + '</span>';
    const journalChip = '<span class="journal-chip">' + escHtml(card.j) + '</span>';
    const metaLabel = card.wk ? '来源' : '期刊';
    const classes = 'event' + (card.ec ? ' ' + escHtml(card.ec) : '');
    const href = String(card.hr || '#').replaceAll('__PAPER_BASE__', base);
    return '<article class="' + classes + '" data-event-scope="lazy" data-search="' + escHtml(item.search || '') + '" data-journal="' + escHtml(item.journal || '') + '" data-fields="' + escHtml(item.fields || '') + '" data-china="' + (item.china ? 'true' : 'false') + '" data-online-today="' + (item.onlineToday ? 'true' : 'false') + '" data-date-type="' + escHtml(item.dateType || '') + '" data-confidence="' + escHtml(item.confidence || '') + '" data-source-type="' + escHtml(item.sourceType || '') + '">\n' +
      '  <div><div class="time">' + escHtml(card.dt) + '</div><div class="date-note">' + escHtml(card.dd) + '</div></div>\n' +
      '  <div>\n' +
      '    <h3><a href="' + escHtml(href) + '">' + escHtml(card.p) + '</a></h3>' + originalTitle + authorsHtml + '\n' +
      '    <div class="meta-block">\n' +
      '      <div class="meta-line"><span class="meta-label">' + metaLabel + '</span><span class="meta-values">' + journalChip + typeTag + detectedChip + '</span></div>\n' +
      '      <div class="meta-line"><span class="meta-label">日期信息</span><span class="meta-values">' + officialChip + (card.lg || '') + '</span></div>\n' +
      '      <div class="meta-line"><span class="meta-label">链接/DOI</span><span class="meta-values">' + link + missingDoi + topics + chinaTag + '</span></div>\n' +
      '    </div>\n' +
      '  </div>\n' +
      '</article>';
  };
  const render = async (list, state, toolbar, replace) => {
    if (replace) {
      clearRendered(list);
      state.rendered = 0;
    }
    const matches = matchingItems(state.items, toolbar, list);
    const empty = list.querySelector('[data-lazy-empty]');
    const hasMoreShards = state.pendingShards.length || (!state.routed && state.nextShard < state.manifest?.shards.length);
    empty.hidden = matches.length > 0;
    if (!matches.length) empty.textContent = hasMoreShards ? '当前批次暂无匹配，点击加载更多继续检索。' : '没有符合当前筛选条件的论文。';
    const start = state.rendered;
    const end = Math.min(start + 40, matches.length);
    const fragment = document.createDocumentFragment();
    for (const item of matches.slice(start, end)) {
      const wrapper = document.createElement('div');
      wrapper.innerHTML = renderCard(item, list.dataset.lazyBase || '.');
      const nodes = Array.from(wrapper.childNodes);
      nodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) node.classList.add('lazy-paper');
      });
      fragment.append(...nodes);
    }
    state.rendered = end;
    list.querySelector('.lazy-more')?.remove();
    list.append(fragment);
    if (state.rendered < matches.length || hasMoreShards) {
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'control lazy-more';
      more.textContent = '加载更多';
      more.setAttribute('aria-label', '加载更多论文');
      more.addEventListener('click', async () => {
        if (state.loadingMore) return;
        state.loadingMore = true;
        try {
          const currentMatches = matchingItems(state.items, toolbar, list);
          if (state.rendered >= currentMatches.length) {
            while (state.pendingShards.length || (!state.routed && state.nextShard < state.manifest?.shards.length)) {
              if (state.routed) await loadNextRoutedShard(state);
              else await loadNextShard(state);
              const nextMatches = matchingItems(state.items, toolbar, list);
              if (nextMatches.length > currentMatches.length) break;
            }
          }
          await render(list, state, toolbar, false);
        } catch (error) {
          showError(list, error);
        } finally {
          state.loadingMore = false;
        }
      });
      list.append(more);
    }
    const counter = document.querySelector('[data-filter-counter="' + (list.dataset.lazyScope || 'default') + '"]');
    if (counter) counter.textContent = '当前显示 ' + matches.length + ' 篇';
  };
  const apply = async (list, state, toolbar) => {
    const requestId = ++state.requestId;
    const tokens = routeTokens(list, toolbar);
    try {
      if (tokens.length) {
        await loadRouted(state, tokens);
      } else {
        state.routed = false;
        state.items = [];
        state.pendingShards = [];
        state.nextShard = 0;
        await loadNextShard(state);
      }
      if (requestId !== state.requestId) return;
      await render(list, state, toolbar, true);
    } catch (error) {
      if (requestId === state.requestId) showError(list, error);
    }
  };
  const initList = (list) => {
    const scope = list.dataset.lazyScope || 'default';
    const toolbar = document.querySelector('.toolbar[data-filter-scope="' + scope + '"]');
    const state = {manifestUrl: new URL(list.dataset.lazyManifest, window.location.href).href, manifestPromise: null, manifest: null, items: [], pendingShards: [], nextShard: 0, rendered: 0, routed: false, requestId: 0, loadingMore: false};
    const rerender = debounce(() => apply(list, state, toolbar), 140);
    for (const role of ['search', 'journal', 'field', 'dateType', 'confidence', 'sourceType']) {
      const control = toolbar?.querySelector('[data-filter-role="' + role + '"]');
      control?.addEventListener(role === 'search' ? 'input' : 'change', rerender);
    }
    const china = toolbar?.querySelector('[data-filter-role="china"]');
    china?.addEventListener('click', () => {
      const active = china.getAttribute('aria-pressed') === 'true';
      china.setAttribute('aria-pressed', String(!active));
      china.classList.toggle('active', !active);
      rerender();
    });
    const params = new URLSearchParams(window.location.search);
    if (toolbar) {
      const search = toolbar.querySelector('[data-filter-role="search"]');
      if (params.get('q')) search.value = params.get('q');
      if (params.get('journal')) toolbar.querySelector('[data-filter-role="journal"]').value = params.get('journal');
      if (params.get('field')) toolbar.querySelector('[data-filter-role="field"]').value = params.get('field');
      if (params.get('dateType')) toolbar.querySelector('[data-filter-role="dateType"]').value = params.get('dateType');
      if (params.get('confidence')) toolbar.querySelector('[data-filter-role="confidence"]').value = params.get('confidence');
      if (params.get('sourceType')) toolbar.querySelector('[data-filter-role="sourceType"]').value = params.get('sourceType');
      if (params.get('china') === '1') {
        const button = toolbar.querySelector('[data-filter-role="china"]');
        button?.setAttribute('aria-pressed', 'true');
        button?.classList.add('active');
      }
    }
    const hasPreset = [...params.keys()].some((key) => ['q', 'journal', 'field', 'dateType', 'confidence', 'sourceType', 'china', 'onlineToday'].includes(key));
    if (list.dataset.lazyDefer !== 'true' || hasPreset) apply(list, state, toolbar);
    list.querySelector('.lazy-start')?.addEventListener('click', () => apply(list, state, toolbar));
  };
  document.querySelectorAll('[data-lazy-list]').forEach(initList);
})();
</script>
""";

def page(
    title: str,
    records: list[dict[str, Any]],
    body: str,
    active: str = "",
    *,
    sidebar_records: list[dict[str, Any]] | None = None,
    sidebar_date: str | None = None,
    show_hero: bool = True,
) -> str:
    lede = secondary_page_lede(title)
    hero = f"""<section class="page-hero">
      <div>
        <p class="page-eyebrow">Research Discovery</p>
        <h1>{html_escape(title)}</h1>
        <p>{html_escape(lede)}</p>
      </div>
      <nav class="context-nav" aria-label="页面导航">{secondary_context_nav(active)}</nav>
    </section>""" if show_hero else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="site-root" content="{BASE}">
  <link rel="icon" type="image/png" href="{BASE}/assets/academic-door-logo.png">
  <meta name="description" content="每日追踪经济学重点期刊与工作论文，区分本站首次发现时间、官方在线日期和中国相关研究。">
  <title>{html_escape(title)}</title>
  {analytics_snippet()}
  <style>{SECONDARY_STYLE}</style>
</head>
<body>
  <a class="skip-link" href="#main-content">跳到正文</a>
  <header class="site-header"><div class="header-inner">
    <a class="wordmark" href="{BASE}/"><small>Academic Door</small>Econ Papers Daily</a>
    <nav class="nav" id="primary-nav">
      <a class="{ 'active' if active == 'home' else '' }" href="{BASE}/">今日</a>
      <a class="{ 'active' if active == 'recent72' else '' }" href="{BASE}/recent72/">最近72小时</a>
      <a class="{ 'active' if active == 'china' else '' }" href="{BASE}/topics/china/">中国研究</a>
      <a class="{ 'active' if active == 'journals' else '' }" href="{BASE}/journals/">期刊</a>
      <a class="{ 'active' if active == 'search' else '' }" href="{BASE}/search/">搜索</a>
      <a class="{ 'active' if active == 'working-papers' else '' }" href="{BASE}/working-papers/">工作论文</a>
      <a href="{BASE}/feed.xml">RSS</a>
    </nav>
    <span class="presence" data-presence-count title="匿名在线人数，按短时心跳统计">在线</span>
    <button class="menu" type="button" aria-controls="primary-nav" aria-expanded="false" aria-label="打开导航">☰</button>
  </div></header>
  <main class="secondary-page" id="main-content">
    {hero}
    <div class="wrap">{body}</div>
    <footer class="site-footer"><div class="footer-inner">
      <div><div class="footer-brand">Academic Door</div><div class="footer-note">读好文献，用好论文。Econ Papers Daily 是每日之门里的研究发现流。</div></div>
      <nav class="footer-links" aria-label="页脚导航"><a href="{BASE}/feed.xml">订阅 RSS</a></nav>
    </div></footer>
  </main>
{menu_script()}{presence_snippet()}{LAZY_LIST_SCRIPT if "data-lazy-list" in body else ""}</body>
</html>
"""


@lru_cache(maxsize=1)
def working_source_title_map() -> dict[str, str]:
    return {
        str(source.get("id") or ""): str(source.get("title") or source.get("id") or "")
        for source in load_working_paper_sources()
        if source.get("id")
    }


def public_source_title(record: dict[str, Any]) -> str:
    """Return public source identity without rewriting canonical venue metadata."""
    if is_working_paper(record):
        source_id = str(record.get("source_id") or record.get("journal_id") or "").removeprefix("source-")
        configured = working_source_title_map().get(source_id)
        if configured:
            return configured
        for key in ("source_name", "series", "journal", "source"):
            value = str(record.get(key) or "").strip()
            if value:
                return value
        return "工作论文"
    return str(record.get("journal") or record.get("source") or "").strip()


def paper_events(records: list[dict[str, Any]], limit: int | None = None, *, scope: str = "default", extra_class: str = "") -> str:
    public = public_records(records)
    if limit is None and len(public) > 40:
        return lazy_list_markup(scope, public, extra_class=extra_class)
    selected = public[:limit] if limit else public
    if not selected:
        return '<div class="empty">暂无符合条件的论文记录。</div>'
    chunks = []
    for record in selected:
        online_today = today_str() in {str(record.get("available_online") or ""), str(record.get("published_online") or "")}
        if record.get("doi"):
            link_or_doi = f'<a class="doi" href="https://doi.org/{html_escape(record.get("doi"))}">{html_escape(record.get("doi"))}</a>'
        elif record.get("url"):
            link_or_doi = f'<a class="doi" href="{html_escape(record.get("url"))}">文章链接</a><span class="doi">暂无 DOI</span>'
        else:
            link_or_doi = '<span class="doi">暂无 DOI</span>'
        topics = article_topics(record)
        source_title = public_source_title(record)
        fields = "".join(f'<span class="pill">{html_escape(topic_label(topic))}</span>' for topic in topics[:3] if topic != "china")
        primary_title, secondary_title = display_titles(record)
        primary_title = primary_title or "未命名记录"
        original_title_html = f'\n    <p class="title-original">{html_escape(secondary_title)}</p>' if secondary_title else ""
        author_text = authors(record)
        authors_html = f'\n    <p class="authors">{html_escape(author_text)}</p>' if author_text else ""
        china_related = is_china_related(record) or "china" in topics
        china_tag = '<span class="pill china">与中国相关</span>' if china_related else ""
        detail_href = detail_url(record)
        official_line = public_date_line(record)
        official_class = "pending" if official_line.startswith("官方日期待补") else ("issue" if public_date_label(record) in {"来源期次", "卷期日期"} else "")
        official_chip = f'<span class="date-chip {official_class}">{html_escape(official_line)}</span>'
        lag_chip = detection_lag_chip(record)
        detected_chip = f'<span class="pill">{html_escape(detected_label(record))}</span>'
        search_text = " ".join(str(value or "") for value in [record.get("title"), record.get("title_zh"), authors(record), source_title, record.get("journal"), record.get("doi")])
        field_attr = " ".join(topics)
        type_tag = f'<span class="pill">{html_escape(source_type_label(record))}</span>' if is_working_paper(record) else ""
        classes = "event" + (f" {extra_class}" if extra_class else "")
        chunks.append(
            f"""<article class="{html_escape(classes)}" data-event-scope="{html_escape(scope)}" data-search="{html_escape(normalize_attr(search_text))}" data-journal="{html_escape(normalize_attr(record.get('journal_id')))}" data-fields="{html_escape(normalize_attr(field_attr))}" data-china="{str(china_related).lower()}" data-online-today="{str(online_today).lower()}" data-date-type="{html_escape(date_type(record))}" data-confidence="{html_escape(confidence_value(record))}" data-source-type="{html_escape(source_type_value(record))}">
  <div><div class="time">{html_escape(detected_time(record) or "—")}</div><div class="date-note">{html_escape(detected_date(record))}</div></div>
  <div>
    <h3><a href="{html_escape(detail_href)}">{html_escape(primary_title)}</a></h3>{original_title_html}{authors_html}
    <div class="meta-block">
      <div class="meta-line"><span class="meta-label">{'来源' if is_working_paper(record) else '期刊'}</span><span class="meta-values"><span class="journal-chip">{html_escape(source_title)}</span>{type_tag}{detected_chip}</span></div>
      <div class="meta-line"><span class="meta-label">日期信息</span><span class="meta-values">{official_chip}{lag_chip}</span></div>
      <div class="meta-line"><span class="meta-label">链接/DOI</span><span class="meta-values">{link_or_doi}{fields}{china_tag}</span></div>
    </div>
  </div>
</article>"""
        )
    return "\n".join(chunks)


FILTER_SCRIPT = """
<script>
(() => {
  const params = new URLSearchParams(window.location.search);
  document.addEventListener('click', (event) => {
    document.querySelectorAll('details.more-filters[open]').forEach((details) => {
      if (!details.contains(event.target)) details.removeAttribute('open');
    });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      document.querySelectorAll('details.more-filters[open]').forEach((details) => details.removeAttribute('open'));
    }
  });
  document.querySelectorAll('.toolbar[data-filter-scope]').forEach((toolbar) => {
    const scope = toolbar.dataset.filterScope || 'default';
    if (document.querySelector('[data-lazy-list][data-lazy-scope="' + scope + '"]')) return;
    const search = toolbar.querySelector('[data-filter-role="search"]');
    const journal = toolbar.querySelector('[data-filter-role="journal"]');
    const field = toolbar.querySelector('[data-filter-role="field"]');
    const dateType = toolbar.querySelector('[data-filter-role="dateType"]');
    const confidence = toolbar.querySelector('[data-filter-role="confidence"]');
    const sourceType = toolbar.querySelector('[data-filter-role="sourceType"]');
    const china = toolbar.querySelector('[data-filter-role="china"]');
    const counter = document.querySelector(`[data-filter-counter="${scope}"]`);
    const empty = document.querySelector(`[data-filter-empty="${scope}"]`);
    const events = Array.from(document.querySelectorAll(`.event[data-event-scope="${scope}"]`));
    if (!search || !journal || !field || !china) return;
    let preset = '';
    if (params.get('q')) search.value = params.get('q');
    if (params.get('journal')) journal.value = params.get('journal');
    if (params.get('field')) field.value = params.get('field');
    if (dateType && params.get('dateType')) dateType.value = params.get('dateType');
    if (confidence && params.get('confidence')) confidence.value = params.get('confidence');
    if (sourceType && params.get('sourceType')) sourceType.value = params.get('sourceType');
    if (params.get('china') === '1') {
      china.setAttribute('aria-pressed', 'true');
      china.classList.add('active');
    }
    if (params.get('onlineToday') === '1') preset = 'online-today';
    function setCounter(visible, chinaOnly) {
      if (!counter) return;
      if (chinaOnly || preset === 'china') {
        counter.innerHTML = `当前显示与中国相关研究 <span class="num">${visible}</span> 篇`;
      } else if (preset === 'online-today') {
        counter.innerHTML = `当前显示在线日期为今日的研究 <span class="num">${visible}</span> 篇`;
      } else {
        counter.innerHTML = `当前显示 <span class="num">${visible}</span> 篇`;
      }
    }
    function applyFilters() {
      const q = (search.value || '').trim().toLowerCase();
      const journalValue = journal.value;
      const fieldValue = field.value;
      const dateTypeValue = dateType ? dateType.value : '';
      const confidenceValue = confidence ? confidence.value : '';
      const sourceTypeValue = sourceType ? sourceType.value : '';
      const chinaOnly = china.getAttribute('aria-pressed') === 'true';
      let visible = 0;
      for (const item of events) {
        const okSearch = !q || item.dataset.search.includes(q);
        const okJournal = !journalValue || item.dataset.journal === journalValue;
        const okField = !fieldValue || item.dataset.fields.split(' ').includes(fieldValue);
        const okDateType = !dateTypeValue || item.dataset.dateType === dateTypeValue;
        const okConfidence = !confidenceValue || item.dataset.confidence === confidenceValue;
        const okSourceType = !sourceTypeValue || item.dataset.sourceType === sourceTypeValue;
        const okChina = (!chinaOnly && preset !== 'china') || item.dataset.china === 'true';
        const okPreset = preset !== 'online-today' || item.dataset.onlineToday === 'true';
        const show = okSearch && okJournal && okField && okDateType && okConfidence && okSourceType && okChina && okPreset;
        item.hidden = !show;
        if (show) visible += 1;
      }
      if (empty) empty.hidden = visible !== 0;
      setCounter(visible, chinaOnly);
    }
    search.addEventListener('input', applyFilters);
    journal.addEventListener('change', applyFilters);
    field.addEventListener('change', applyFilters);
    if (dateType) dateType.addEventListener('change', applyFilters);
    if (confidence) confidence.addEventListener('change', applyFilters);
    if (sourceType) sourceType.addEventListener('change', applyFilters);
    china.addEventListener('click', () => {
      const active = china.getAttribute('aria-pressed') !== 'true';
      china.setAttribute('aria-pressed', String(active));
      china.classList.toggle('active', active);
      applyFilters();
    });
    document.querySelectorAll(`[data-filter-preset][data-filter-scope-target="${scope}"]`).forEach((item) => {
      item.addEventListener('click', (event) => {
        event.preventDefault();
        preset = item.dataset.filterPreset || '';
        if (preset === 'all') {
          search.value = '';
          journal.value = '';
          field.value = '';
          if (dateType) dateType.value = '';
          if (confidence) confidence.value = '';
          if (sourceType) sourceType.value = '';
          china.setAttribute('aria-pressed', 'false');
          china.classList.remove('active');
        }
        if (preset === 'china') {
          china.setAttribute('aria-pressed', 'true');
          china.classList.add('active');
        }
        applyFilters();
        toolbar.scrollIntoView({behavior: 'smooth', block: 'start'});
      });
    });
    applyFilters();
  });
  document.querySelectorAll('[data-filter-preset]:not([data-filter-scope-target])').forEach((item) => {
    item.setAttribute('data-filter-scope-target', 'default');
  });
  for (const item of document.querySelectorAll('[data-filter-preset]')) {
    if (!item.dataset.boundScopeFallback) {
      item.dataset.boundScopeFallback = '1';
      if (!item.dataset.filterScopeTarget) {
        item.dataset.filterScopeTarget = 'default';
      }
    }
  }
})();
</script>
"""


def filter_toolbar(records: list[dict[str, Any]], *, include_rss: bool = False, source_label: str = "筛选期刊", scope: str = "default") -> str:
    if not records:
        return ""
    journals = sorted(
        {
            (record.get("journal_id"), public_source_title(record))
            for record in records
            if record.get("journal_id") and public_source_title(record)
        },
        key=lambda item: item[1],
    )
    topics = sorted({topic for record in records for topic in article_topics(record)}, key=topic_label)
    date_types = sorted({date_type(record) for record in records}, key=date_type_label)
    confidences = sorted({confidence_value(record) for record in records})
    source_types = sorted({source_type_value(record) for record in records})
    journal_options = "".join(f'<option value="{html_escape(jid)}">{html_escape(title)}</option>' for jid, title in journals)
    field_options = "".join(f'<option value="{html_escape(topic)}">{html_escape(topic_label(topic))}</option>' for topic in topics)
    date_type_options = "".join(f'<option value="{html_escape(value)}">{html_escape(date_type_label(value))}</option>' for value in date_types)
    confidence_options = "".join(f'<option value="{html_escape(value)}">{html_escape(confidence_label(value))}</option>' for value in confidences)
    source_type_options = "".join(f'<option value="{html_escape(value)}">{html_escape(SOURCE_TYPE_LABELS.get(value, source_type_label({"source_type": value})))}</option>' for value in source_types)
    source_type_control = f'<select class="control" aria-label="筛选来源类型" data-filter-role="sourceType"><option value="">筛选来源类型</option>{source_type_options}</select>' if len(source_types) > 1 else ""
    advanced_controls = []
    advanced_controls.append(f'<select class="control" aria-label="筛选日期类型" data-filter-role="dateType"><option value="">日期类型</option>{date_type_options}</select>')
    advanced_controls.append(f'<select class="control" aria-label="筛选可信度" data-filter-role="confidence"><option value="">可信度</option>{confidence_options}</select>')
    if source_type_control:
        advanced_controls.append(source_type_control)
    advanced_html = f'<details class="control more-filters"><summary>高级筛选</summary><div class="more-filters-row">{"".join(advanced_controls)}</div></details>'
    return f"""<div class="toolbar" id="filters-{html_escape(scope)}" data-filter-scope="{html_escape(scope)}">
  <input class="control" aria-label="搜索标题、作者或 DOI" data-filter-role="search" type="search" placeholder="搜索">
  <select class="control" aria-label="筛选期刊" data-filter-role="journal"><option value="">{html_escape(source_label)}</option>{journal_options}</select>
  <select class="control" aria-label="筛选主题" data-filter-role="field"><option value="">主题</option>{field_options}</select>
  {advanced_html}
  <button class="control toggle" data-filter-role="china" type="button" aria-pressed="false">中国</button>
</div>
<div class="empty" data-filter-empty="{html_escape(scope)}" hidden>没有符合当前筛选条件的论文。</div>"""


def date_from_record(record: dict[str, Any]) -> datetime | None:
    value = detected_date(record)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def recent_records(records: list[dict[str, Any]], days: int = 7) -> list[dict[str, Any]]:
    dates = [date_from_record(record) for record in records]
    dates = [item for item in dates if item is not None]
    if not dates:
        return records
    cutoff = max(dates) - timedelta(days=days - 1)
    return [record for record in records if (date_from_record(record) or datetime.min) >= cutoff]


def recent_detected_records(records: list[dict[str, Any]], days: int = 3) -> list[dict[str, Any]]:
    """Records first detected recently, excluding dated backfill items.

    A source can expose an old catalogue item during a fresh crawl.  Its
    ``detected_at`` is recent, but its official date proves it is not a recent
    discovery signal, so it must stay in the archive rather than the 72-hour
    selection page.
    """
    try:
        today = datetime.fromisoformat(today_str()).date()
    except ValueError:
        return []
    cutoff = today - timedelta(days=max(days, 1) - 1)
    selected: list[dict[str, Any]] = []
    for record in records:
        try:
            detected = datetime.fromisoformat(detected_date(record)).date()
        except ValueError:
            continue
        if cutoff <= detected <= today:
            official = official_date(record)
            try:
                if official and date.fromisoformat(official[:10]) < cutoff:
                    continue
            except ValueError:
                pass
            selected.append(record)
    return sort_records(selected)


def journal_view_links(journal_id: str, journal_records: list[dict[str, Any]], today_records: list[dict[str, Any]]) -> str:
    latest_day = detected_date(journal_records[0]) if journal_records else today_str()
    today_count = len([record for record in today_records if record.get("journal_id") == journal_id])
    latest_count = len([record for record in journal_records if detected_date(record) == latest_day])
    recent_count = len(recent_records(journal_records, 7))
    today_label = f"今日 {today_count}" if today_count else f"最新日期 {latest_count}"
    target_day = today_str() if today_count else latest_day
    return f"""<div class="toolbar">
  <a class="control primary" href="{BASE}/daily/{html_escape(target_day)}/?journal={html_escape(journal_id)}">{html_escape(today_label)}</a>
  <a class="control" href="{BASE}/journals/{html_escape(journal_id)}/recent7/">最近 7 天 {recent_count}</a>
  <a class="control" href="{BASE}/journals/{html_escape(journal_id)}/">全部历史 {len(journal_records)}</a>
</div>"""


def topic_view_links(topic: str, topic_records: list[dict[str, Any]], today_records: list[dict[str, Any]]) -> str:
    topic_today = [record for record in today_records if topic in article_topics(record)]
    latest_day = detected_date(topic_records[0]) if topic_records else today_str()
    latest_count = len([record for record in topic_records if detected_date(record) == latest_day])
    recent = recent_records(topic_records, 7)
    target_day = today_str() if topic_today else latest_day
    today_label = f"今日 {len(topic_today)}" if topic_today else f"最新日期 {latest_count}"
    return f"""<div class="toolbar">
  <a class="control primary" href="{BASE}/daily/{html_escape(target_day)}/?field={html_escape(topic)}">{html_escape(today_label)}</a>
  <a class="control" href="{BASE}/topics/{html_escape(topic)}/recent7/">最近 7 天 {len(recent)}</a>
  <a class="control" href="{BASE}/topics/{html_escape(topic)}/">全部历史 {len(topic_records)}</a>
</div>"""


def home_body(records: list[dict[str, Any]], today_records: list[dict[str, Any]]) -> str:
    flow_records = [record for record in today_records if is_today_home_flow_record(record)]
    journal_flow_records = [record for record in flow_records if not is_working_paper(record)]
    working_flow_records = [record for record in flow_records if is_working_paper(record)]
    all_working = working_paper_records(records)
    all_journal_count = sum(1 for record in records if not is_working_paper(record) and has_public_title(record))
    recent72_records = recent_detected_records(records, 3)
    s = stats(records, today_records, flow_records)
    freshness_class = "warn" if s["last_run_freshness"] != "状态正常" else ""
    flow_date = today_str()
    journal_note = ""
    working_note = ""
    journal_note_html = f"<p>{journal_note}</p>" if journal_note else ""
    working_note_html = f"<p>{working_note}</p>" if working_note else ""
    note = ""
    return f"""<section class="banner">
  <div class="banner-main">
      <div class="hero-layout">
      <div>
        <p class="eyebrow">TOP economics journals, updated daily</p>
        <h1>{SITE_NAME}</h1>
        <p>{SITE_SUBTITLE}</p>
        <div class="hero-stats">
          <a class="hero-stat" href="#journal-flow" data-filter-preset="all" data-filter-scope-target="journal"><strong>{len(journal_flow_records)}</strong><span>今日期刊论文新发现</span></a>
          <a class="hero-stat china" href="#journal-flow" data-filter-preset="china" data-filter-scope-target="journal"><strong>{sum(1 for record in journal_flow_records if is_china_related(record))}</strong><span>期刊论文中与中国相关</span></a>
          <a class="hero-stat" href="{BASE}/recent72/"><strong>{len(recent72_records)}</strong><span>最近72小时</span></a>
          <a class="hero-stat" href="#working-flow" data-filter-preset="all" data-filter-scope-target="working"><strong>{len(working_flow_records)}</strong><span>今日工作论文</span></a>
          <a class="hero-stat china" href="#working-flow" data-filter-preset="china" data-filter-scope-target="working"><strong>{sum(1 for record in working_flow_records if is_public_china_related(record))}</strong><span>工作论文中与中国相关</span></a>
          <a class="hero-stat duo" href="{BASE}/search/"><span class="stat-title">累计监测</span><div class="hero-stat-pair"><div><strong>{all_journal_count}</strong><em>期刊论文</em></div><div><strong>{len(all_working)}</strong><em>工作论文</em></div></div></a>
        </div>
      </div>
      <aside class="operator-card">
        <img src="{BASE}/assets/academic-portal-qr.jpg" alt="学术传送门二维码">
        <div>
          <strong>学术传送门</strong>
          <span>本站由学术传送门运营</span>
          <em>读好文献，用好文献</em>
        </div>
      </aside>
      </div>
  </div>
</section>
<section class="status-strip">
  <span>最近监测 <strong>{html_escape(s['last_run'])}</strong></span>
  <span class="{freshness_class}">监测状态 <strong>{html_escape(s['last_run_freshness'])}</strong></span>
  <span>监测类型 <strong>{html_escape(s['last_run_label'])}</strong></span>
  <span>下次快速 <strong>{html_escape(s['next_light_run'])}</strong></span>
  <span>下次全量 <strong>{html_escape(s['next_full_run'])}</strong></span>
</section>
<section id="journal-flow" class="section-head"><div><h2>今日 TOP 期刊论文 <span class="live-count" data-filter-counter="journal"></span></h2>{journal_note_html}</div><p>{html_escape(flow_date)}</p></section>
<section class="stats">
  <a class="stat" href="{BASE}/journals/"><strong>{len({record.get('journal_id') for record in journal_flow_records if record.get('journal_id')})}</strong><span>今日涉及期刊</span></a>
  <a class="stat china" href="#journal-flow" data-filter-preset="china" data-filter-scope-target="journal"><strong>{sum(1 for record in journal_flow_records if is_china_related(record))}</strong><span>期刊论文中与中国相关</span></a>
  <a class="stat" href="#journal-flow" data-filter-preset="online-today" data-filter-scope-target="journal"><strong>{sum(1 for record in journal_flow_records if today_str() in {str(record.get('available_online') or ''), str(record.get('published_online') or '')})}</strong><span>期刊在线日期为今日</span></a>
  <a class="stat" href="{BASE}/journals/"><strong>{all_journal_count}</strong><span>累计期刊论文记录</span></a>
</section>
{filter_toolbar(journal_flow_records, include_rss=True, scope="journal")}
{note}
{paper_events(journal_flow_records, scope="journal")}
<section id="working-flow" class="section-head split-section"><div><h2>今日工作论文 <span class="live-count" data-filter-counter="working"></span></h2>{working_note_html}</div><p><a href="{BASE}/working-papers/today/">查看全部 {len(working_flow_records)} 篇</a></p></section>
<section class="stats">
  <a class="stat" href="{BASE}/working-papers/today/"><strong>{len(working_flow_records)}</strong><span>今日工作论文</span></a>
  <a class="stat china" href="#working-flow" data-filter-preset="china" data-filter-scope-target="working"><strong>{sum(1 for record in working_flow_records if is_public_china_related(record))}</strong><span>工作论文中与中国相关</span></a>
  <a class="stat" href="{BASE}/sources/working-papers/"><strong>{len({record.get('journal_id') for record in working_flow_records if record.get('journal_id')})}</strong><span>今日涉及来源</span></a>
  <a class="stat" href="{BASE}/working-papers/"><strong>{len(all_working)}</strong><span>累计工作论文记录</span></a>
</section>
{filter_toolbar(working_flow_records, source_label="筛选来源", scope="working")}
{paper_events(working_flow_records, scope="working", extra_class="home-wp-preview")}
{FILTER_SCRIPT}
"""


def working_papers_body(records: list[dict[str, Any]], *, view: str = "all") -> str:
    all_wp_records = working_paper_records(records)
    wp_records = all_wp_records
    if view == "today":
        wp_records = recent_detected_records(all_wp_records, 1)
    elif view == "recent7":
        wp_records = recent_detected_records(all_wp_records, 7)
    elif view == "china":
        wp_records = [record for record in all_wp_records if is_public_china_related(record)]
    elif view == "china-recent7":
        wp_records = [record for record in recent_detected_records(all_wp_records, 7) if is_public_china_related(record)]
    latest_day = detected_date(wp_records[0]) if wp_records else ""
    today_count = len(recent_detected_records(all_wp_records, 1))
    recent_count = len(recent_detected_records(all_wp_records, 7))
    china_count = sum(1 for record in all_wp_records if is_public_china_related(record))
    title = {
        "today": "今日工作论文",
        "recent7": "最近 7 天工作论文",
        "china": "与中国相关工作论文",
        "china-recent7": "最近 7 天与中国相关工作论文",
    }.get(view, "全部工作论文")
    note = "覆盖工作论文与机构研究来源，按本站首次发现时间倒序排列；官方日期与本站首次发现日期分开显示。"
    tabs = [
        ("today", "今日", f"{BASE}/working-papers/today/", today_count),
        ("recent7", "最近 7 天", f"{BASE}/working-papers/recent7/", recent_count),
        ("all", "全部", f"{BASE}/working-papers/", len(all_wp_records)),
        ("china", "与中国相关", f"{BASE}/working-papers/china/", china_count),
    ]
    tabs_html = "".join(
        f'<a class="view-tab {"active" if key == view else ""}" href="{href}">{label} <strong>{count}</strong></a>'
        for key, label, href, count in tabs
    )
    return f"""<section class="section-head">
  <div><h2>{title} <span class="live-count" id="flowCounter"></span></h2><p>{note}</p></div>
  <p>{html_escape(latest_day or today_str())}</p>
</section>
<nav class="view-tabs">{tabs_html}</nav>
<section class="stats">
  <a class="stat" href="{BASE}/working-papers/"><strong>{len(all_wp_records)}</strong><span>累计工作论文记录</span></a>
  <a class="stat" href="{BASE}/working-papers/china/"><strong>{china_count}</strong><span>与中国相关</span></a>
  <a class="stat" href="{BASE}/working-papers/today/"><strong>{today_count}</strong><span>今日新发现</span></a>
  <a class="stat" href="{BASE}/sources/working-papers/"><strong>{len(load_working_paper_sources())}</strong><span>监测来源</span></a>
</section>
{filter_toolbar(wp_records, source_label="筛选来源")}
{paper_events(wp_records)}
{FILTER_SCRIPT}
"""


def china_topic_body(records: list[dict[str, Any]], topic_records: list[dict[str, Any]], today_records: list[dict[str, Any]]) -> str:
    public_topic_records = unique_records(public_records(topic_records))
    journal_records = [record for record in public_topic_records if not is_working_paper(record)]
    wp_records = [record for record in public_topic_records if is_working_paper(record)]
    today_journals = [record for record in journal_records if record_is_on_date(record, today_str())]
    today_wp = [record for record in wp_records if record_is_on_date(record, today_str())]
    recent_journals = recent_records(journal_records, 7)
    recent_wp = recent_records(wp_records, 7)
    return f"""<section class="section-head">
  <div><h2>与中国相关</h2><p>期刊论文和工作论文分开浏览，优先展示明确涉及中国数据、制度、市场或研究对象的记录。</p></div>
  <p>{len(public_topic_records)} 篇</p>
</section>
<section class="stats">
  <a class="stat" href="#china-journals"><strong>{len(journal_records)}</strong><span>期刊论文</span></a>
  <a class="stat" href="#china-working"><strong>{len(wp_records)}</strong><span>工作论文</span></a>
  <a class="stat" href="{BASE}/daily/{today_str()}/?field=china"><strong>{len(today_journals)}</strong><span>今日期刊论文</span></a>
  <a class="stat" href="{BASE}/working-papers/china/"><strong>{len(today_wp)}</strong><span>今日工作论文</span></a>
</section>
<section id="china-journals" class="section-head"><div><h2>与中国相关：期刊论文 <span class="live-count" data-filter-counter="china-journal"></span></h2></div><p>最近 7 天 {len(recent_journals)} 篇</p></section>
{filter_toolbar(journal_records, include_rss=True, scope="china-journal")}
{paper_events(journal_records, scope="china-journal")}
<section id="china-working" class="section-head split-section"><div><h2>与中国相关：工作论文 <span class="live-count" data-filter-counter="china-working"></span></h2></div><p>最近 7 天 {len(recent_wp)} 篇</p></section>
{filter_toolbar(wp_records, source_label="筛选来源", scope="china-working")}
{paper_events(wp_records, scope="china-working")}
{FILTER_SCRIPT}
"""


def china_quality_body(records: list[dict[str, Any]]) -> str:
    latest = public_records(records[:500])
    confirmed = [record for record in latest if is_china_related(record)]
    candidates = [record for record in latest if record.get("china_relevance_status") == "candidate"]
    rejected = [
        record
        for record in latest
        if str(record.get("china_relevance_status") or "").lower() in {"rejected", "excluded", "none"}
        or record.get("china_related") is False
    ]
    working_confirmed = [record for record in confirmed if is_working_paper(record)]

    def item(record: dict[str, Any]) -> str:
        title_primary, title_secondary = display_titles(record)
        title_primary = title_primary or "未命名记录"
        secondary = f'<p class="title-original">{html_escape(title_secondary)}</p>' if title_secondary else ""
        status = str(record.get("china_relevance_status") or ("confirmed" if is_china_related(record) else "none"))
        reason = record.get("china_relevance_reason") or record.get("china_related_reason") or "暂无判定说明"
        evidence = record.get("china_relevance_evidence") or record.get("china_related_source") or ""
        return f"""<article class="audit-item">
  <h3><a href="{html_escape(record_url(record))}">{html_escape(title_primary)}</a></h3>
  {secondary}
  <div class="audit-meta">{html_escape(record.get('journal') or '')} · {html_escape(detected_date(record))} · 状态：{html_escape(status)}</div>
  <div class="audit-reason"><b>判定理由</b>：{html_escape(reason)}</div>
  {f'<div class="audit-reason"><b>证据</b>：{html_escape(evidence)}</div>' if evidence else ''}
</article>"""

    confirmed_html = "".join(item(record) for record in confirmed[:25]) or '<div class="empty">暂无已确认记录。</div>'
    candidates_html = "".join(item(record) for record in candidates[:25]) or '<div class="empty">暂无候选记录。</div>'
    rejected_html = "".join(item(record) for record in rejected[:25]) or '<div class="empty">暂无排除样本。</div>'
    return f"""<section class="section-head">
  <div><h2>中国相关判定抽检</h2><p>集中查看 AI/规则判定结果，帮助校准“与中国相关”的召回率和误判率。</p></div>
  <p>最近样本 {len(latest)} 条</p>
</section>
<section class="audit-grid">
  <div class="audit-card"><strong>{len(confirmed)}</strong><span>最近样本中已确认中国相关</span></div>
  <div class="audit-card"><strong>{len(working_confirmed)}</strong><span>其中工作论文/机构研究</span></div>
  <div class="audit-card"><strong>{len(candidates)}</strong><span>待校准候选</span></div>
</section>
<nav class="view-tabs">
  <a class="view-tab active" href="#confirmed">已确认</a>
  <a class="view-tab" href="#candidates">候选</a>
  <a class="view-tab" href="#rejected">排除样本</a>
</nav>
<section id="confirmed" class="section-head"><div><h2>已确认中国相关</h2><p>公开页面只展示已确认结果；有争议样本优先留在候选或排除样本中。</p></div></section>
<div class="audit-list">{confirmed_html}</div>
<section id="candidates" class="section-head"><div><h2>候选记录</h2><p>这里用于发现漏判/误判模式，后续可继续接入摘要增强判定。</p></div></section>
<div class="audit-list">{candidates_html}</div>
<section id="rejected" class="section-head"><div><h2>排除样本</h2><p>抽查被排除记录，避免规则过严导致中国相关研究漏掉。</p></div></section>
<div class="audit-list">{rejected_html}</div>"""

def lazy_list_markup(
    scope: str,
    records: list[dict[str, Any]],
    *,
    extra_class: str = "",
    manifest_dataset_id: str | None = None,
    filter_tokens: list[str] | None = None,
) -> str:
    public = unique_records(public_records(records))
    keys = [detail_key(record) for record in public if detail_key(record)]
    dataset_id = manifest_dataset_id or hashlib.sha256((extra_class + "\n" + "\n".join(keys)).encode("utf-8")).hexdigest()[:16]
    if manifest_dataset_id is None:
        LAZY_DATASETS.setdefault(dataset_id, (public, extra_class))
    deferred = "true" if scope == "search" else "false"
    filter_attr = f' data-lazy-filter="{html_escape(" ".join(filter_tokens))}"' if filter_tokens else ""
    return (
        f'<div class="lazy-list" data-lazy-list data-lazy-scope="{html_escape(scope)}" '
        f'data-lazy-base="{BASE}" data-lazy-defer="{deferred}" '
        f'data-lazy-manifest="{BASE}/paper-index/{dataset_id}/manifest.json"{filter_attr}>'
        f'<div class="lazy-initial">{paper_events(public[:10], scope=scope)}</div>'
        f'<button class="control lazy-start" type="button">浏览全部 {len(public)} 篇</button>'
        '<div class="empty" data-lazy-empty hidden>没有符合当前筛选条件的论文。</div></div>'
    )


def register_lazy_dataset(scope: str, records: list[dict[str, Any]], extra_class: str = "") -> str:
    public = search_catalog_records(records) if scope == "search" else unique_records(public_records(records))
    keys = [detail_key(record) for record in public if detail_key(record)]
    dataset_id = hashlib.sha256((extra_class + "\n" + "\n".join(keys)).encode("utf-8")).hexdigest()[:16]
    LAZY_DATASETS.setdefault(dataset_id, (public, extra_class))
    return dataset_id


def scoped_shared_events(
    scope: str,
    scoped_records: list[dict[str, Any]],
    all_records: list[dict[str, Any]],
    filter_tokens: list[str],
    *,
    extra_class: str = "",
) -> str:
    public = unique_records(public_records(scoped_records))
    if len(public) <= 40:
        return paper_events(scoped_records, scope=scope, extra_class=extra_class)
    search_id = register_lazy_dataset("search", all_records)
    return lazy_list_markup(
        scope,
        scoped_records,
        extra_class=extra_class,
        manifest_dataset_id=search_id,
        filter_tokens=filter_tokens,
    )


def lazy_route_tokens(value: str) -> set[str]:
    normalized = normalize_attr(value).lower()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for word in list(tokens):
        if len(word) >= 3:
            tokens.update(word[index:index + 3] for index in range(len(word) - 2))
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        tokens.update(run)
        tokens.update(run[index] for index in range(len(run)))
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def route_bucket(token: str) -> int:
    first = ord(token[0])
    second = ord(token[1]) if len(token) > 1 else 0
    return (first * 31 + second) % ROUTE_BUCKETS


def write_lazy_indexes(docs_dir: Path) -> None:
    """Write compact catalogs, routed metadata, and content shards."""
    index_root = docs_dir / "paper-index"
    for dataset_id, (records, extra_class) in LAZY_DATASETS.items():
        dataset_dir = index_root / dataset_id
        route_dir = dataset_dir / "route"
        shard_dir = dataset_dir / "shards"
        shard_dir.mkdir(parents=True, exist_ok=True)
        routed = len(records) > ROUTE_SKIP_SHARD_LIMIT * LAZY_SHARD_SIZE
        if routed:
            route_dir.mkdir(parents=True, exist_ok=True)
        shards: dict[str, list[dict[str, Any]]] = defaultdict(list)
        routes: dict[str, set[str]] = defaultdict(set)
        for position, record in enumerate(records):
            key = detail_key(record)
            topics = article_topics(record)
            shard = f"{position // LAZY_SHARD_SIZE:04d}"
            search_text = " ".join(
                str(value or "")
                for value in [
                    record.get("title"), record.get("title_zh"), authors(record),
                    public_source_title(record), record.get("journal"), record.get("doi"), record.get("url"),
                    " ".join(topic_label(topic) for topic in topics), " ".join(topics),
                ]
            )
            online_today = today_str() in {
                str(record.get("available_online") or ""),
                str(record.get("published_online") or ""),
            }
            metadata = {
                "key": key,
                "shard": shard,
                "search": normalize_attr(search_text),
                "journal": normalize_attr(record.get("journal_id")),
                "fields": normalize_attr(" ".join(topics)),
                "china": bool(is_china_related(record) or "china" in topics),
                "onlineToday": online_today,
                "dateType": date_type(record),
                "confidence": confidence_value(record),
                "sourceType": source_type_value(record),
            }
            primary_title, secondary_title = display_titles(record)
            author_text = authors(record)
            official_line = public_date_line(record)
            official_class = "pending" if official_line.startswith("官方日期待补") else ("issue" if public_date_label(record) in {"来源期次", "卷期日期"} else "")
            metadata["card"] = {
                "p": primary_title or "未命名记录",
                "s": secondary_title or "",
                "a": author_text,
                "d": record.get("doi") or "",
                "u": record.get("url") or "",
                "j": public_source_title(record),
                "tp": [topic_label(topic) for topic in topics[:3] if topic != "china"],
                "cn": metadata["china"],
                "on": online_today,
                "dt": detected_time(record) or "—",
                "dd": detected_date(record),
                "dl": detected_label(record),
                "od": official_line,
                "oc": official_class,
                "lg": detection_lag_chip(record),
                "st": source_type_label(record) if is_working_paper(record) else "",
                "wk": is_working_paper(record),
                "hr": detail_url(record).replace(BASE, "__PAPER_BASE__"),
                "ec": extra_class,
            }
            shards[shard].append(metadata)
            if routed:
                route_values = lazy_route_tokens(search_text)
                route_values.add("journal:" + normalize_attr(record.get("journal_id")))
                route_values.update("field:" + topic for topic in topics)
                route_values.update("field:" + field for field in (record.get("fields") or []))
                route_values.update({
                    "date:" + date_type(record),
                    "confidence:" + confidence_value(record),
                    "source:" + source_type_value(record),
                })
                if metadata["china"]:
                    route_values.add("china")
                if online_today:
                    route_values.add("online-today")
                for token in route_values:
                    if token:
                        routes[token].add(shard)
        manifest = {
            "version": 2,
            "count": len(records),
            "routed": routed,
            "shards": [{"name": shard, "count": len(items)} for shard, items in sorted(shards.items())],
        }
        write_text(dataset_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))
        for shard, items in shards.items():
            write_text(shard_dir / f"{shard}.json", json.dumps(items, ensure_ascii=False, separators=(",", ":")))
        if routed:
            bucketed: dict[int, dict[str, str]] = defaultdict(dict)
            for token, shards in routes.items():
                bucketed[route_bucket(token)][token] = ",".join(sorted(shards))
            for bucket, payload in sorted(bucketed.items()):
                write_text(route_dir / f"{bucket:03d}.json", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def search_body(records: list[dict[str, Any]]) -> str:
    searchable = search_catalog_records(records)
    journal_records = [record for record in searchable if not is_working_paper(record)]
    wp_records = [record for record in searchable if is_working_paper(record)]
    return f"""<section class="section-head">
  <div><h2>全站检索</h2><p>搜索全部历史记录；首页搜索只筛选当天论文流。</p></div>
  <p>{len(searchable)} 条</p>
</section>
<section class="stats">
  <span class="stat"><strong>{len(journal_records)}</strong><span>期刊论文</span></span>
  <span class="stat"><strong>{len(wp_records)}</strong><span>工作论文/机构研究</span></span>
  <span class="stat"><strong>{len({detected_date(record) for record in searchable})}</strong><span>记录日期</span></span>
  <a class="stat china" href="{BASE}/topics/china/"><strong>{sum(1 for record in searchable if is_public_china_related(record))}</strong><span>与中国相关</span></a>
</section>
<div class="empty home-note">可以按标题、中文标题、作者、DOI、期刊/来源检索；也可以继续组合期刊、主题、日期类型、可信度和来源类型筛选。</div>
{filter_toolbar(searchable, include_rss=True, source_label="筛选期刊/来源", scope="search")}
{paper_events(searchable, scope="search")}
{FILTER_SCRIPT}"""


def recent72_body(records: list[dict[str, Any]]) -> str:
    recent = recent_detected_records(public_records(records), 3)
    journal_records = [record for record in recent if not is_working_paper(record)]
    wp_records = [record for record in recent if is_working_paper(record)]
    china_count = sum(1 for record in recent if is_public_china_related(record))
    dates = sorted({detected_date(record) for record in recent if detected_date(record)}, reverse=True)
    date_label = " / ".join(dates) if dates else "暂无记录"
    return f"""<section class="section-head">
  <div><h2>最近72小时</h2><p>按本站首次发现日期展示今天及前两天记录，适合连续查看近 3 天选题池。</p></div>
  <p>{html_escape(date_label)}</p>
</section>
<section class="stats">
  <span class="stat"><strong>{len(recent)}</strong><span>近3天新发现</span></span>
  <span class="stat"><strong>{len(journal_records)}</strong><span>期刊论文</span></span>
  <span class="stat"><strong>{len(wp_records)}</strong><span>工作论文</span></span>
  <a class="stat china" href="{BASE}/topics/china/"><strong>{china_count}</strong><span>与中国相关</span></a>
  <a class="stat" href="{BASE}/export/recent72.ris"><strong>RIS</strong><span>Zotero 导入</span></a>
  <a class="stat" href="{BASE}/export/recent72.bib"><strong>BibTeX</strong><span>文献导出</span></a>
</section>
{filter_toolbar(recent, include_rss=True, source_label="筛选期刊/来源", scope="recent72")}
{paper_events(recent, scope="recent72")}
{FILTER_SCRIPT}"""


def admin_status_body(records: list[dict[str, Any]]) -> str:
    token_hash = os.environ.get("ADMIN_STATUS_TOKEN_HASH", "").strip()
    status = load_status()
    workflow = status.get("workflow") or {}
    sources = status.get("sources") or {}
    failures = [source_id for source_id, item in sorted(sources.items()) if not item.get("ok")]
    wp_sources = [source_id for source_id in sources if str(source_id).startswith("working-paper:")]
    low_confidence = sum(1 for record in records if (record.get("date_confidence") or "F") in {"D", "F", "unknown"})
    china_count = sum(1 for record in records if is_china_related(record))
    today_records = [record for record in records if record_is_on_date(record, today_str())]
    body = f"""<section class="section-head">
  <div><h2>线上后台状态</h2><p>GitHub Pages 无法提供真正登录鉴权；这里仅发布公开安全摘要，敏感审核仍使用本地后台。</p></div>
</section>
{monitor_summary_cards(records, today_records)}
<section class="audit-grid">
  <div class="audit-card"><strong>{len(records)}</strong><span>累计监测记录</span></div>
  <div class="audit-card"><strong>{china_count}</strong><span>已确认中国相关</span></div>
  <div class="audit-card"><strong>{low_confidence}</strong><span>低可信日期样本</span></div>
  <div class="audit-card"><strong>{len(wp_sources)}</strong><span>工作论文来源状态</span></div>
  <div class="audit-card"><strong>{len(failures)}</strong><span>失败/受限来源</span></div>
  <div class="audit-card"><strong>{html_escape(beijing_stamp(workflow.get('finished_at')))}</strong><span>最近监测完成</span></div>
</section>
<section class="section-head"><div><h2>后续私有化建议</h2><p>如需知道具体访问者或登录后访问，建议部署到 Cloudflare Access / Vercel + Auth，而不是纯 GitHub Pages。</p></div></section>
<div class="empty">当前公开页只放聚合状态，不放 API key、审核 token、原始后台操作入口或访问者身份信息。</div>"""
    if not token_hash:
        return f"""<section class="section-head">
  <div><h2>线上后台状态</h2><p>尚未启用线上后台 token。为避免公开未成熟后台，当前只提供本地后台。</p></div>
</section>
<div class="gate">
  <h3>未启用公开后台</h3>
  <p>请继续使用本地后台：<code>local_admin/status.html</code> 和 <code>http://127.0.0.1:8765/</code>。</p>
  <p class="gate-note">若以后需要线上查看，可在 GitHub Secrets 设置 <code>ADMIN_STATUS_TOKEN_HASH</code> 后重新运行 workflow。注意：静态页面 token 只能防误点，不等于真正登录鉴权。</p>
</div>"""
    return f"""<div id="gate" class="gate">
  <h3>输入后台访问 token</h3>
  <p class="gate-note">这是静态页面轻保护，只用于避免普通访客误入；不应放敏感数据。</p>
  <input id="adminToken" type="password" placeholder="访问 token">
  <button id="unlockAdmin" type="button">进入</button>
  <p id="gateError" class="gate-note"></p>
</div>
<div id="adminContent" class="hidden">{body}</div>
<script>
async function sha256(text) {{
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
}}
async function unlock() {{
  const token = document.getElementById('adminToken').value || localStorage.getItem('epd_admin_token') || '';
  const hash = await sha256(token);
  if (hash === '{html_escape(token_hash)}') {{
    localStorage.setItem('epd_admin_token', token);
    document.getElementById('gate').classList.add('hidden');
    document.getElementById('adminContent').classList.remove('hidden');
  }} else {{
    document.getElementById('gateError').textContent = 'token 不正确。';
  }}
}}
document.getElementById('unlockAdmin').addEventListener('click', unlock);
if (localStorage.getItem('epd_admin_token')) unlock();
</script>"""


def admin_status_body(records: list[dict[str, Any]]) -> str:
    """Render a concise product-health dashboard for the protected status page."""
    token_hash = os.environ.get("ADMIN_STATUS_TOKEN_HASH", "").strip()
    status = load_status()
    workflow = status.get("workflow") or {}
    sources = status.get("sources") or {}
    source_groups = status.get("source_groups") or {}
    failures = [source_id for source_id, item in sorted(sources.items()) if not item.get("ok")]
    wp_sources = [source_id for source_id in sources if str(source_id).startswith("working-paper:")]
    confidence_counts = Counter(str(record.get("date_confidence") or "unknown") for record in records)
    date_source_counts = Counter(str(record.get("date_source") or "unknown") for record in records)
    low_confidence = sum(1 for record in records if str(record.get("date_confidence") or "F") in {"D", "F", "unknown"})
    china_count = sum(1 for record in records if is_china_related(record))
    today_records = [record for record in records if record_is_on_date(record, today_str())]
    today_journals = sum(1 for record in today_records if not is_working_paper(record))
    today_wp = sum(1 for record in today_records if is_working_paper(record))
    crossref_new_deposits = sum(
        1
        for record in today_records
        if "created" in str(record.get("date_source") or "").casefold()
        or "created" in str((record.get("raw_data") or {}).get("crossref_date_source") or "").casefold()
    )
    crossref_fallback_today = sum(1 for record in today_records if "crossref" in str(record.get("date_source") or "").casefold())
    cn_group = source_groups.get("cn-journals") or {}
    cnki_group = source_groups.get("cnki-rss") or {}
    publisher_group = source_groups.get("publisher-detail") or {}
    source_health = read_json(DATA_DIR / "source_health.json", {})
    coverage_counts = source_health.get("coverage_counts") or {}
    health_rows = "".join(
        f"<tr><td>{html_escape(key)}</td><td>{html_escape(value)}</td></tr>"
        for key, value in (
            ("双路径/专项可用", coverage_counts.get("official_or_specialized", 0)),
            ("补充路径可用", coverage_counts.get("supplemental", 0)),
            ("仅 Crossref 备用", coverage_counts.get("crossref_only", 0)),
            ("不可用", coverage_counts.get("unavailable", 0)),
        )
    )
    ingestion = read_json(DATA_DIR / "ingestion_audit.json", {})
    recent72_audit = read_json(DATA_DIR / "recent72_coverage_audit.json", {})
    journals_by_id = journal_lookup()
    delay_rows = source_delay_rows(records, 14)
    suspected_rows = "".join(
        "<tr>"
        f"<td>{html_escape(item.get('source'))}</td>"
        f"<td>{html_escape(item.get('new_candidate_count', item.get('raw_count', 0)))}</td>"
        f"<td>{html_escape(item.get('daily_count', 0))}</td>"
        f"<td>{html_escape('; '.join(str(value) for value in item.get('examples', [])[:3]))}</td>"
        f"<td>{html_escape(item.get('reason'))}</td>"
        "</tr>"
        for item in ingestion.get("suspected_missed_sources", [])[:20]
        if isinstance(item, dict)
    ) or '<tr><td colspan="5">暂无明显风险</td></tr>'

    cn_rows = "".join(
        f"""<tr><td>期刊官网</td><td>{html_escape((journals_by_id.get(str(item.get('journal_id') or '')) or {}).get('title') or item.get('journal'))}</td><td>{html_escape(item.get('count'))}</td><td></td><td></td><td></td><td>{html_escape(item.get('mode'))}</td><td>{html_escape(item.get('message'))}</td></tr>"""
        for item in cn_group.get("journals", [])
    )
    cnki_rows = "".join(
        f"""<tr><td>CNKI RSS</td><td>{html_escape((journals_by_id.get(str(item.get('journal_id') or '')) or {}).get('title') or item.get('journal'))}</td><td>{html_escape(item.get('count'))}</td><td>{html_escape(item.get('filtered'))}</td><td>{html_escape(item.get('stale'))}</td><td>{html_escape(item.get('latest_research_date') or item.get('latest_research'))}</td><td>{html_escape(item.get('mode'))}</td><td>{html_escape(item.get('message'))}</td></tr>"""
        for item in cnki_group.get("journals", [])
    )
    cn_status_rows = cn_rows + cnki_rows or '<tr><td colspan="8">暂无中文期刊状态</td></tr>'
    publisher_rows = "".join(
        f"""<tr><td>{html_escape(item.get('publisher'))}</td><td>{html_escape(item.get('attempted'))}</td><td>{html_escape(item.get('changed'))}</td><td>{html_escape(item.get('ab_dates'))}</td><td>{html_escape(item.get('message'))}</td></tr>"""
        for item in publisher_group.get("publishers", [])
    ) or '<tr><td colspan="5">暂无出版社详情页状态</td></tr>'
    confidence_rows = "".join(
        f"<tr><td>{html_escape(confidence_label(key))}</td><td>{value}</td></tr>"
        for key, value in sorted(confidence_counts.items())
    )
    date_source_rows = "".join(
        f"<tr><td>{html_escape(key)}</td><td>{value}</td></tr>"
        for key, value in date_source_counts.most_common(12)
    )
    failure_rows = "".join(
        f"<tr><td>{html_escape(source_id)}</td><td>{html_escape((sources.get(source_id) or {}).get('message'))}</td></tr>"
        for source_id in failures[:20]
    ) or '<tr><td colspan="2">暂无失败来源</td></tr>'
    recent72_missing_rows = "".join(
        "<tr>"
        f"<td>{html_escape(item.get('source'))}</td>"
        f"<td>{html_escape(item.get('count'))}</td>"
        f"<td>{html_escape('; '.join(str(value) for value in item.get('examples', [])[:3]))}</td>"
        f"<td>{html_escape(item.get('reason'))}</td>"
        "</tr>"
        for item in recent72_audit.get("missing_by_source", [])[:20]
        if isinstance(item, dict)
    ) or '<tr><td colspan="4">暂无疑似遗漏</td></tr>'

    body = f"""<section class="section-head">
  <div><h2>线上后台状态</h2><p>公开安全摘要。敏感审核与人工确认仍使用本地后台。</p></div>
</section>
{monitor_summary_cards(records, today_records)}
<section class="audit-grid">
  <div class="audit-card"><strong>{len(records)}</strong><span>累计监测记录</span></div>
  <div class="audit-card"><strong>{today_journals}</strong><span>今日期刊论文</span></div>
  <div class="audit-card"><strong>{today_wp}</strong><span>今日工作论文</span></div>
  <div class="audit-card"><strong>{china_count}</strong><span>已确认中国相关</span></div>
  <div class="audit-card"><strong>{low_confidence}</strong><span>低可信日期样本</span></div>
  <div class="audit-card"><strong>{crossref_new_deposits}</strong><span>今日 Crossref 新入库</span></div>
  <div class="audit-card"><strong>{crossref_fallback_today}</strong><span>今日 Crossref/备用日期</span></div>
  <div class="audit-card"><strong>{len(wp_sources)}</strong><span>工作论文来源状态</span></div>
  <div class="audit-card"><strong>{len(failures)}</strong><span>失败/受限来源</span></div>
  <div class="audit-card"><strong>{html_escape(beijing_stamp(workflow.get('finished_at')))}</strong><span>最近监测完成</span></div>
</section>
<section class="section-head"><div><h2>正式期刊信源覆盖</h2><p>这是后台维护指标：区分官方/专项路径与 Crossref 备用，不把备用路径伪装成双路径稳定。</p></div></section>
<table class="journal-table"><thead><tr><th>覆盖等级</th><th>期刊数</th></tr></thead><tbody>{health_rows}</tbody></table>
<section class="section-head"><div><h2>重点出版社延迟对比</h2><p>最近 14 天内，比较 RSS/TOC、出版社详情页与 Crossref 备用日期对第一时间发现的贡献。</p></div></section>
<table class="journal-table"><thead><tr><th>出版社</th><th>记录</th><th>有官方日期</th><th>无精确日期</th><th>延迟&gt;2天</th><th>平均滞后</th><th>最大滞后</th><th>RSS/TOC</th><th>详情页</th><th>Crossref fallback</th></tr></thead><tbody>{delay_rows}</tbody></table>
<section class="section-head"><div><h2>入库诊断</h2><p>对比今日原始候选和最终展示记录，用于判断是否存在“抓到但未入库”。</p></div></section>
<table class="journal-table"><thead><tr><th>指标</th><th>当前值</th><th>说明</th></tr></thead><tbody>
<tr><td>诊断日期</td><td>{html_escape(ingestion.get('date') or today_str())}</td><td>与今日页使用同一个北京时间日期。</td></tr>
<tr><td>原始候选</td><td>{html_escape(ingestion.get('raw_candidates', '未生成'))}</td><td>RSS、Crossref、中文官网、工作论文等原始抓取候选总数。</td></tr>
<tr><td>今日展示记录</td><td>{html_escape(ingestion.get('daily_records', len(today_records)))}</td><td>去重和清理后进入今日页面的记录。</td></tr>
<tr><td>已见过候选</td><td>{html_escape(ingestion.get('already_seen_candidates', '未生成'))}</td><td>raw 中已在 seen/历史归档出现的记录，不再算今日首次发现。</td></tr>
<tr><td>今日新候选</td><td>{html_escape(ingestion.get('new_today_candidates', '未生成'))}</td><td>去重后仍符合“今日首次发现”归档日期的候选。</td></tr>
<tr><td>归入其他日期</td><td>{html_escape(ingestion.get('new_other_date_candidates', '未生成'))}</td><td>首次抓到但官方日期指向其他日期，因此进入对应日期归档。</td></tr>
<tr><td>被压制候选</td><td>{html_escape(ingestion.get('suppressed_candidates', '未生成'))}</td><td>多为 RSS/目录回流、无精确日期或不适合作为今日新发现的记录。</td></tr>
<tr><td>疑似漏入库</td><td>{html_escape(ingestion.get('new_today_missing_candidates', '未生成'))}</td><td>看起来应进入今日页、但未在今日公开文件中找到的候选。</td></tr>
<tr><td>RSS 无精确日期候选</td><td>{html_escape(ingestion.get('rss_without_precise_date_candidates', '未生成'))}</td><td>已抓到但只有卷期或待解析日期的 RSS 记录。</td></tr>
<tr><td>RSS 无精确日期入库</td><td>{html_escape(ingestion.get('rss_without_precise_date_daily', '未生成'))}</td><td>作为“今日新发现”展示，但不等同于今日 online。</td></tr>
<tr><td>历史回流清理</td><td>{html_escape(ingestion.get('seen_backflow_removed', 0))}</td><td>已在 seen 中存在、但因 RSS/目录回流再次出现的旧记录；不进入今日首次发现。</td></tr>
</tbody></table>
<section class="section-head"><div><h2>今日疑似漏抓源</h2><p>只统计“未在历史 seen 中出现、且看起来应进入今日归档”的新候选；已见过旧文和日期不合格记录不再作为漏抓报警。</p></div></section>
<table class="journal-table"><thead><tr><th>来源</th><th>新候选</th><th>今日入库</th><th>样例</th><th>说明</th></tr></thead><tbody>{suspected_rows}</tbody></table>
<p>A 为官方渠道明确日期，B 为官方渠道推断；C/D/F 需要继续补强。</p>
<table class="journal-table"><thead><tr><th>可信度</th><th>数量</th></tr></thead><tbody>{confidence_rows}</tbody></table>
<section class="section-head"><div><h2>日期来源</h2><p>用于判断“今日新发现”和“官方/在线日期”的证据链。</p></div></section>
<table class="journal-table"><thead><tr><th>来源</th><th>数量</th></tr></thead><tbody>{date_source_rows}</tbody></table>
<section class="section-head"><div><h2>中文期刊状态</h2><p>官网抓取、CNKI RSS 和本地补充分开看；旧期次只进入归档，不进入首页今日流。</p></div></section>
<table class="journal-table"><thead><tr><th>来源链路</th><th>期刊</th><th>接受/数量</th><th>过滤</th><th>滞后/旧项</th><th>最新研究日期</th><th>模式</th><th>说明</th></tr></thead><tbody>{cn_status_rows}</tbody></table>
<section class="section-head"><div><h2>漏抓风险提示</h2><p>用于判断当天是否可能因为上游入库延迟、出版社限制或中文源滞后造成少抓。</p></div></section>
<table class="journal-table"><thead><tr><th>风险项</th><th>当前值</th><th>说明</th></tr></thead><tbody>
<tr><td>今日归档记录</td><td>{len(today_records)}</td><td>为 0 时需要检查上游是否延迟或任务是否被取消。</td></tr>
<tr><td>Crossref newly deposited</td><td>{crossref_new_deposits}</td><td>按 Crossref 入库时间补抓的新 DOI。</td></tr>
<tr><td>Crossref/备用日期</td><td>{crossref_fallback_today}</td><td>出版社详情页受限时，日期主要依赖 Crossref 元数据。</td></tr>
<tr><td>CNKI RSS 接受量</td><td>{html_escape(cnki_group.get('count', 0))}</td><td>可补中文期刊，但部分期刊可能滞后于官网。</td></tr>
<tr><td>最近72小时原始候选</td><td>{html_escape(recent72_audit.get('raw_candidates', '未生成'))}</td><td>最近 3 天 raw 候选总数，用于判断外部源是否有候选输入。</td></tr>
<tr><td>最近72小时疑似遗漏</td><td>{html_escape(recent72_audit.get('eligible_missing_candidates', '未生成'))}</td><td>raw 中未见过、应进入最近 72 小时但不在公开归档中的候选。</td></tr>
</tbody></table>
<section class="section-head"><div><h2>最近72小时覆盖审计</h2><p>对照 raw 候选、历史 seen 和公开 daily 文件，检查抓到但未展示的风险。</p></div></section>
<table class="journal-table"><thead><tr><th>来源</th><th>疑似遗漏</th><th>样例</th><th>说明</th></tr></thead><tbody>{recent72_missing_rows}</tbody></table>
<section class="section-head"><div><h2>出版社日期解析</h2><p>统计详情页、RSS、Crossref fallback 对 online date 的贡献。</p></div></section>
<table class="journal-table"><thead><tr><th>出版社</th><th>尝试</th><th>更新</th><th>A/B 日期</th><th>状态</th></tr></thead><tbody>{publisher_rows}</tbody></table>
<section class="section-head"><div><h2>失败/受限来源</h2><p>只显示聚合原因，不展示密钥或敏感信息。</p></div></section>
<table class="journal-table"><thead><tr><th>来源</th><th>原因</th></tr></thead><tbody>{failure_rows}</tbody></table>"""

    if not token_hash:
        return """<section class="section-head">
  <div><h2>线上后台状态</h2><p>尚未启用线上后台 token。当前继续使用本地后台。</p></div>
</section>
<div class="gate">
  <h3>未启用公开后台</h3>
  <p>请继续使用本地后台：<code>local_admin/status.html</code> 和 <code>http://127.0.0.1:8765/</code>。</p>
  <p class="gate-note">如需线上查看，可设置 <code>ADMIN_STATUS_TOKEN_HASH</code> 后重新运行 workflow。静态页面 token 只适合轻保护，不等于真正登录鉴权。</p>
</div>"""
    return f"""<div id="gate" class="gate">
  <h3>输入后台访问 token</h3>
  <p class="gate-note">这是静态页面轻保护，只用于避免普通访客误入；不放敏感数据。</p>
  <input id="adminToken" type="password" placeholder="访问 token">
  <button id="unlockAdmin" type="button">进入</button>
  <p id="gateError" class="gate-note"></p>
</div>
<div id="adminContent" class="hidden">{body}</div>
<script>
async function sha256(text) {{
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
}}
async function unlock() {{
  const token = document.getElementById('adminToken').value || localStorage.getItem('epd_admin_token') || '';
  const hash = await sha256(token);
  if (hash === '{html_escape(token_hash)}') {{
    localStorage.setItem('epd_admin_token', token);
    document.getElementById('gate').classList.add('hidden');
    document.getElementById('adminContent').classList.remove('hidden');
  }} else {{
    document.getElementById('gateError').textContent = 'token 不正确。';
  }}
}}
document.getElementById('unlockAdmin').addEventListener('click', unlock);
if (localStorage.getItem('epd_admin_token')) unlock();
</script>"""


def write_page(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    relative_parent = path.parent.relative_to(DOCS_DIR)
    depth = len(relative_parent.parts)
    page_base = "." if depth == 0 else "/".join([".."] * depth)
    write_text(path, content.replace(BASE, page_base))


def archive_compatibility_page() -> str:
    """Keep old /archive/ links useful without presenting a second archive UI."""
    return f'''<!doctype html>
<html lang="zh-CN"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,follow">
  <meta http-equiv="refresh" content="0;url={BASE}/search/">
  <title>历史记录检索 · Econ Papers Daily</title>
</head><body>
  <main><h1>历史记录已移至全站检索</h1>
  <p><a href="{BASE}/search/">前往全站检索</a></p></main>
</body></html>'''


def ensure_today_archive(daily_dir: Path) -> None:
    """Keep the static site from carrying yesterday as "today" after midnight."""
    path = daily_dir / f"{today_str()}.json"
    records = read_json(path, [])
    if not isinstance(records, list):
        raise SystemExit(f"{path} is not a daily record list")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, json.dumps(records, ensure_ascii=False, indent=2) + "\n")


def markdown_table_to_html(lines: list[str]) -> str:
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    body_rows = lines[2:]
    head = "".join(f"<th>{html_escape(cell)}</th>" for cell in header)
    rows = []
    for line in body_rows:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append("<tr>" + "".join(f"<td>{html_escape(cell)}</td>" for cell in cells) + "</tr>")
    return f'<table class="journal-table"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def markdown_to_body(markdown: str) -> str:
    blocks: list[str] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("# "):
            blocks.append(f'<section class="section-head"><div><h2>{html_escape(line[2:].strip())}</h2><p>按研究领域整理经济学重点期刊，便于跟踪最新论文与安排阅读优先级。</p></div></section>')
            i += 1
            continue
        if line.startswith("## "):
            blocks.append(f'<section class="section-head"><div><h2>{html_escape(line[3:].strip())}</h2></div></section>')
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and set(lines[i + 1].replace("|", "").replace("-", "").replace(" ", "")) <= {":"}:
            table_lines = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            blocks.append(markdown_table_to_html(table_lines))
            continue
        blocks.append(f"<p>{html_escape(line)}</p>")
        i += 1
    return "\n".join(blocks)


def export_year(record: dict[str, Any]) -> str:
    date_value = str(record.get("available_online") or record.get("published_online") or record.get("issue_date") or detected_date(record) or "")
    return date_value[:4] if len(date_value) >= 4 else ""


def bibtex_key(record: dict[str, Any]) -> str:
    first_author = "paper"
    author_text = authors(record)
    if author_text and author_text != "Unknown Authors":
        first_author = re.sub(r"[^A-Za-z0-9]+", "", author_text.split(",")[0].split()[-1]) or "paper"
    title_word = re.sub(r"[^A-Za-z0-9]+", "", str(record.get("title") or "").split()[0] if record.get("title") else "item")
    return f"{first_author}{export_year(record) or 'nd'}{title_word}"


def ris_text(records: list[dict[str, Any]]) -> str:
    chunks = []
    for record in public_records(records):
        ty = "WORK" if is_working_paper(record) else "JOUR"
        lines = [f"TY  - {ty}", f"TI  - {record.get('title') or ''}"]
        if record.get("title_zh") and record.get("title_zh") != record.get("title"):
            lines.append(f"T2  - {record.get('title_zh')}")
        for name in record.get("authors") or []:
            lines.append(f"AU  - {name}")
        if record.get("journal"):
            lines.append(f"JO  - {record.get('journal')}")
        if record.get("doi"):
            lines.append(f"DO  - {record.get('doi')}")
        lines.append(f"UR  - {record_url(record)}")
        if export_year(record):
            lines.append(f"PY  - {export_year(record)}")
        if official_date(record):
            lines.append(f"Y2  - {official_date(record)}")
        lines.append("ER  -")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + "\n"


def bibtex_escape(value: Any) -> str:
    return str(value or "").replace("{", "\\{").replace("}", "\\}")


def bibtex_text(records: list[dict[str, Any]]) -> str:
    chunks = []
    used: Counter[str] = Counter()
    for record in public_records(records):
        entry_type = "techreport" if is_working_paper(record) else "article"
        base_key = bibtex_key(record)
        used[base_key] += 1
        key = base_key if used[base_key] == 1 else f"{base_key}{used[base_key]}"
        fields = {
            "title": record.get("title"),
            "author": " and ".join(record.get("authors") or []),
            "journal": record.get("journal"),
            "year": export_year(record),
            "doi": record.get("doi"),
            "url": record_url(record),
            "note": public_date_line(record),
        }
        body = ",\n".join(f"  {name} = {{{bibtex_escape(value)}}}" for name, value in fields.items() if value)
        chunks.append(f"@{entry_type}{{{key},\n{body}\n}}")
    return "\n\n".join(chunks) + "\n"


def write_exports(docs_dir: Path, records: list[dict[str, Any]]) -> None:
    export_dir = docs_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    write_text(export_dir / "recent72.ris", ris_text(records))
    write_text(export_dir / "recent72.bib", bibtex_text(records))


def detail_item(record: dict[str, Any]) -> dict[str, Any]:
    """Return the stable public detail payload used by static and JS views."""
    key = detail_key(record)
    title_primary, title_secondary = display_titles(record)
    return {
        "key": key,
        "title": record.get("title") or "",
        "title_zh": record.get("title_zh") or "",
        "title_primary": title_primary,
        "title_secondary": title_secondary,
        "authors": authors(record),
        "source": public_source_title(record),
        "source_type": source_type_label(record),
        "detected": detected_date(record),
        "detected_time": detected_time(record),
        "official": public_date_line(record),
        "accepted": record.get("accepted_date") or "",
        "topics": [topic_label(topic) for topic in article_topics(record)],
        "abstract": record.get("abstract") or "",
        "abstract_zh": record.get("abstract_zh") or "",
        "abstract_status": record.get("abstract_status") or "",
        "doi": record.get("doi") or "",
        "url": record.get("url") or "",
    }


def static_detail_body(item: dict[str, Any]) -> str:
    """Render core detail content directly into HTML for no-JS reading."""
    primary = item.get("title_primary") or item.get("title_zh") or item.get("title") or "未命名记录"
    secondary = item.get("title_secondary") or ""
    if secondary and str(secondary).casefold() != str(primary).casefold():
        secondary_html = f'<p class="detail-title-secondary">{html_escape(secondary)}</p>'
    else:
        secondary_html = ""

    topics = "".join(f'<span class="pill">{html_escape(topic)}</span>' for topic in item.get("topics") or [])
    if not topics:
        topics = '<span class="muted">暂无主题标签</span>'

    doi = str(item.get("doi") or "")
    doi_html = (
        f'<a href="https://doi.org/{html_escape(doi)}" target="_blank" rel="noreferrer">DOI：{html_escape(doi)}</a>'
        if doi
        else '<span class="muted">暂无 DOI</span>'
    )
    original_url = str(item.get("url") or "")
    original_html = (
        f'<a class="primary" href="{html_escape(original_url)}" target="_blank" rel="noreferrer">打开原文页面</a>'
        if original_url and original_url != "#"
        else ""
    )

    abstract = str(item.get("abstract") or "")
    if abstract:
        abstract_html = f'<section class="detail-abstract"><h2>摘要</h2><p>{html_escape(abstract)}</p></section>'
    else:
        status = str(item.get("abstract_status") or "").casefold()
        if any(token in status for token in ("blocked", "forbidden", "proxy")):
            message = "出版社页面暂时无法读取摘要，系统会继续尝试补全。"
        elif any(token in status for token in ("no-abstract", "not-exposed", "route-missing")):
            message = "上游暂未公开摘要，系统会继续尝试补全。"
        else:
            message = "摘要暂未公开，系统会继续尝试补全。"
        abstract_html = f'<section class="detail-abstract"><h2>摘要</h2><p class="muted">{html_escape(message)}</p></section>'

    abstract_zh = str(item.get("abstract_zh") or "")
    abstract_zh_html = (
        f'<section class="detail-abstract"><h2>中文摘要</h2><p>{html_escape(abstract_zh)}</p></section>'
        if abstract_zh and abstract_zh != abstract
        else ""
    )
    accepted = str(item.get("accepted") or "")
    accepted_html = (
        f'<div class="label">接受日期</div><div>{html_escape(accepted)} · 编辑流程日期，不等同于正式上线</div>'
        if accepted
        else ""
    )
    detected = str(item.get("detected") or "")
    detected_time_value = str(item.get("detected_time") or "")
    detected_label = detected + (f" {detected_time_value}" if detected_time_value else "")

    return f'''<article class="detail-page" id="paperRoot">
  <p class="detail-kicker"><a href="{BASE}/">Econ Papers Daily</a> / {html_escape(item.get("source_type"))}</p>
  <h1>{html_escape(primary)}</h1>
  {secondary_html}
  <p class="detail-authors">{html_escape(item.get("authors"))}</p>
  <div class="detail-links">{original_html}{doi_html}</div>
  <div class="detail-meta">
    <div class="label">来源</div><div>{html_escape(item.get("source"))} · {html_escape(item.get("source_type"))}</div>
    <div class="label">本站首次发现</div><div>{html_escape(detected_label)}</div>
    <div class="label">日期信息</div><div>{html_escape(item.get("official"))}</div>
    {accepted_html}
    <div class="label">主题</div><div class="meta-values">{topics}</div>
  </div>
  {abstract_html}
  {abstract_zh_html}
</article>'''


def write_static_detail_pages(docs_dir: Path, records: list[dict[str, Any]]) -> list[Path]:
    """Generate static-first detail routes for every unique public record."""
    detail_root = docs_dir / "paper"
    if detail_root.exists():
        for path in sorted(detail_root.glob("*/index.html")):
            path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                pass

    paths: list[Path] = []
    for record in unique_records(public_records(records)):
        item = detail_item(record)
        primary = item.get("title_primary") or item.get("title_zh") or item.get("title") or "论文详情"
        path = detail_root / str(item["key"]) / "index.html"
        write_page(path, page(str(primary), records, static_detail_body(item), show_hero=False))
        paths.append(path)

    total_bytes = sum(path.stat().st_size for path in paths)
    print(f"Static detail pages: routes={len(paths)} bytes={total_bytes}")
    return paths


def write_detail_data(docs_dir: Path, records: list[dict[str, Any]]) -> None:
    """Write compact, sharded detail payloads so detail pages stay fast."""
    detail_dir = docs_dir / "paper-data"
    detail_dir.mkdir(parents=True, exist_ok=True)
    shards: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in unique_records(public_records(records)):
        key = detail_key(record)
        title_primary, title_secondary = display_titles(record)
        item = {
            "key": key,
            "title": record.get("title") or "",
            "title_zh": record.get("title_zh") or "",
            "title_primary": title_primary,
            "title_secondary": title_secondary,
            "authors": authors(record),
            "source": record.get("journal") or record.get("source") or "",
            "source_type": source_type_label(record),
            "detected": detected_date(record),
            "detected_time": detected_time(record),
            "official": public_date_line(record),
            "accepted": record.get("accepted_date") or "",
            "topics": [topic_label(topic) for topic in article_topics(record)],
            "abstract": record.get("abstract") or "",
            "abstract_zh": record.get("abstract_zh") or "",
            "abstract_status": record.get("abstract_status") or "",
            "doi": record.get("doi") or "",
            "url": record.get("url") or "",
        }
        # paper.html routes by the first two characters of the 12-char hash
        # suffix, keeping each request small and cacheable.
        shards[key[-12:-10].lower()].append(item)
    for shard in range(256):
        name = f"{shard:02x}.json"
        write_text(detail_dir / name, json.dumps(shards.get(name[:2], []), ensure_ascii=False, separators=(",", ":")))


def paper_detail_body() -> str:
    """Return the client-rendered detail view owned by the secondary renderer."""
    return r'''<article class="detail-page detail-loading" id="paperRoot" aria-busy="true">
  <p class="detail-kicker">Econ Papers Daily / 论文详情</p>
  <h1>正在载入论文详情</h1>
</article>
<script>
(() => {
  const key = new URLSearchParams(window.location.search).get('key') || '';
  const root = document.getElementById('paperRoot');
  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const abstractMessage = (status) => {
    const value = String(status || '').toLowerCase();
    if (value.includes('blocked') || value.includes('forbidden') || value.includes('proxy')) return '出版社页面暂时无法读取摘要，系统会继续尝试补全。';
    if (value.includes('no-abstract') || value.includes('not-exposed') || value.includes('route-missing')) return '上游暂未公开摘要，系统会继续尝试补全。';
    return '摘要暂未公开，系统会继续尝试补全。';
  };
  const message = (title, copy) => {
    root.classList.remove('detail-loading');
    root.removeAttribute('aria-busy');
    root.innerHTML = `<p class="detail-kicker">Econ Papers Daily / 论文详情</p><h1>${escapeHTML(title)}</h1><p class="muted">${escapeHTML(copy)}</p><div class="detail-links"><a class="primary" href="./search/">进入全站检索</a><a href="./">返回首页</a></div>`;
  };
  const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const loadShard = async (shard, attempts = 3) => {
    let lastError;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const response = await fetch(`./paper-data/${shard}.json?v=${Date.now()}-${attempt}`, {cache: 'no-cache'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!Array.isArray(data)) throw new Error('Invalid shard payload');
        return data;
      } catch (error) {
        lastError = error;
        if (attempt < attempts - 1) await wait(350 * (attempt + 1));
      }
    }
    throw lastError || new Error('Shard unavailable');
  };
  const load = async () => {
    const match = key.match(/([0-9a-f]{12})$/i);
    if (!match) return message('未找到这篇论文', '该链接无效，或记录已被去重。');
    try {
      const shard = match[1].slice(0, 2).toLowerCase();
      let data = await loadShard(shard);
      let item = data.find((record) => record.key === key);
      if (!item) {
        await wait(450);
        data = await loadShard(shard, 2);
        item = data.find((record) => record.key === key);
      }
      if (!item) return message('未找到这篇论文', '该记录可能已被合并，请通过全站检索查找最新版本。');
      const primary = item.title_primary || item.title_zh || item.title || '未命名记录';
      const secondary = item.title_secondary && item.title_secondary.toLowerCase() !== primary.toLowerCase()
        ? `<p class="detail-title-secondary">${escapeHTML(item.title_secondary)}</p>` : '';
      document.title = `${primary} | Econ Papers Daily`;
      const topics = (item.topics || []).map((topic) => `<span class="pill">${escapeHTML(topic)}</span>`).join('');
      const doi = item.doi ? `<a href="https://doi.org/${encodeURI(item.doi)}" target="_blank" rel="noreferrer">DOI：${escapeHTML(item.doi)}</a>` : '';
      const missingDoi = item.doi ? '' : '<span class="muted">暂无 DOI</span>';
      const original = item.url && item.url !== '#' ? `<a class="primary" href="${escapeHTML(item.url)}" target="_blank" rel="noreferrer">打开原文页面</a>` : '';
      const abstract = item.abstract ? `<section class="detail-abstract"><h2>摘要</h2><p>${escapeHTML(item.abstract)}</p></section>` : `<section class="detail-abstract"><h2>摘要</h2><p class="muted">${escapeHTML(abstractMessage(item.abstract_status))}</p></section>`;
      const abstractZh = item.abstract_zh && item.abstract_zh !== item.abstract ? `<section class="detail-abstract"><h2>中文摘要</h2><p>${escapeHTML(item.abstract_zh)}</p></section>` : '';
      const accepted = item.accepted ? `<div class="label">接受日期</div><div>${escapeHTML(item.accepted)} · 编辑流程日期，不等同于正式上线</div>` : '';
      root.classList.remove('detail-loading');
      root.removeAttribute('aria-busy');
      const detectedLabel = item.detected + (item.detected_time ? ' ' + item.detected_time : '');
      root.innerHTML = `<p class="detail-kicker"><a href="./">Econ Papers Daily</a> / ${escapeHTML(item.source_type)}</p><h1>${escapeHTML(primary)}</h1>${secondary}<p class="detail-authors">${escapeHTML(item.authors)}</p><div class="detail-links">${original}${doi}${missingDoi}</div><div class="detail-meta"><div class="label">来源</div><div>${escapeHTML(item.source)} · ${escapeHTML(item.source_type)}</div><div class="label">本站首次发现</div><div>${escapeHTML(detectedLabel)}</div><div class="label">日期信息</div><div>${escapeHTML(item.official)}</div>${accepted}<div class="label">主题</div><div class="meta-values">${topics || '<span class="muted">暂无主题标签</span>'}</div></div>${abstract}${abstractZh}`;
    } catch (error) {
      message('论文详情暂时无法载入', '请刷新页面重试，或进入全站检索。');
    }
  };
  load();
})();
</script>'''


def is_future_daily_route(value: str, today_value: str) -> bool:
    try:
        return date.fromisoformat(value) > date.fromisoformat(today_value)
    except ValueError:
        return False


def remove_future_daily_routes(docs_dir: Path, today_value: str) -> None:
    daily_root = docs_dir / "daily"
    if not daily_root.exists():
        return
    for route_dir in daily_root.iterdir():
        if route_dir.is_dir() and is_future_daily_route(route_dir.name, today_value):
            shutil.rmtree(route_dir)


def main() -> None:
    global DOCS_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-dir", type=Path, default=DATA_DIR / "daily")
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    args = parser.parse_args()

    # Secondary-page rendering is read-only against the data-line interface.
    # Empty-day records are created by the data workflow, not by this renderer.
    args.docs_dir = args.docs_dir.resolve()
    DOCS_DIR = args.docs_dir
    LAZY_DATASETS.clear()
    records = load_all_daily(args.daily_dir)
    register_lazy_dataset("search", public_records(records))
    current_day = today_str()
    today_records = [record for record in records if record_is_on_date(record, current_day)]
    home_flow_records = [record for record in today_records if is_today_home_flow_record(record)]
    home_flow_date = current_day
    recent72_records = recent_detected_records(records, 3)
    write_exports(args.docs_dir, recent72_records)
    write_detail_data(args.docs_dir, records)
    write_static_detail_pages(args.docs_dir, records)
    write_page(
        args.docs_dir / "paper.html",
        page("论文详情", records, paper_detail_body(), show_hero=False),
    )
    write_page(
        args.docs_dir / "recent72" / "index.html",
        page(
            "最近72小时",
            records,
            recent72_body(records),
            active="recent72",
            sidebar_records=recent72_records[:40] or home_flow_records,
            sidebar_date=home_flow_date,
        ),
    )
    write_page(
        args.docs_dir / "sources" / "working-papers" / "index.html",
        page(
            "工作论文来源",
            records,
            working_paper_sources_body(records),
            active="working-papers",
            sidebar_records=home_flow_records,
            sidebar_date=home_flow_date,
        ),
    )
    write_page(
        args.docs_dir / "search" / "index.html",
        page(
            "全站检索",
            records,
            search_body(records),
            active="search",
            sidebar_records=home_flow_records,
            sidebar_date=home_flow_date,
        ),
    )
    wp_records = working_paper_records(records)
    write_page(
        args.docs_dir / "working-papers" / "index.html",
        page(
            "全部工作论文",
            records,
            working_papers_body(records),
            active="working-papers",
            sidebar_records=wp_records[:40] or home_flow_records,
            sidebar_date=home_flow_date,
        ),
    )
    write_page(
        args.docs_dir / "working-papers" / "today" / "index.html",
        page(
            "今日工作论文",
            records,
            working_papers_body(records, view="today"),
            active="working-papers",
            sidebar_records=[record for record in wp_records if record_is_on_date(record, current_day)][:40],
            sidebar_date=current_day,
        ),
    )
    write_page(
        args.docs_dir / "working-papers" / "recent7" / "index.html",
        page(
            "最近 7 天工作论文",
            records,
            working_papers_body(records, view="recent7"),
            active="working-papers",
            sidebar_records=recent_records(wp_records, 7)[:40] or wp_records[:40] or home_flow_records,
            sidebar_date=home_flow_date,
        ),
    )
    write_page(
        args.docs_dir / "working-papers" / "china" / "index.html",
        page(
            "与中国相关工作论文",
            records,
            working_papers_body(records, view="china"),
            active="working-papers",
            sidebar_records=[record for record in wp_records if is_public_china_related(record)][:40] or wp_records[:40] or home_flow_records,
            sidebar_date=home_flow_date,
        ),
    )

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_journal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        # seen-only records remain searchable and available in the full
        # catalogue, but must not manufacture a daily archive entry from their
        # historical first_seen timestamp.
        if not record.get("_from_seen_only"):
            by_date[detected_date(record) or record.get("_daily_date") or "unknown"].append(record)
        if not is_working_paper(record) and record.get("journal_id"):
            by_journal[str(record.get("journal_id"))].append(record)
        for field in record.get("fields", []) or []:
            by_field[field].append(record)
        for topic in article_topics(record):
            by_topic[topic].append(record)
    by_date.setdefault(current_day, [])
    for path in sorted(args.daily_dir.glob("*.json")):
        if not is_future_daily_route(path.stem, current_day):
            by_date.setdefault(path.stem, [])

    remove_future_daily_routes(args.docs_dir, current_day)

    archive_links = []
    archive_rows = []
    for daily_date, daily_records in sorted(by_date.items(), reverse=True):
        if is_future_daily_route(daily_date, current_day):
            continue
        journal_count = sum(1 for record in daily_records if not is_working_paper(record))
        working_count = sum(1 for record in daily_records if is_working_paper(record))
        official_summary = archive_official_date_summary(daily_records)
        body = (
            f'<section class="section-head"><div><h2>{html_escape(daily_date)} 监测记录</h2>'
            f'<p>本站首次发现日期：{html_escape(daily_date)}；官方/在线日期范围：{html_escape(official_summary)}。支持按期刊、主题、日期类型、可信度和“与中国相关”筛选。</p></div></section>'
            f'{filter_toolbar(daily_records)}{paper_events(daily_records)}{FILTER_SCRIPT}'
        )
        write_page(
            args.docs_dir / "daily" / daily_date / "index.html",
            page(f"{daily_date} 归档", records, body, active="archive", sidebar_records=daily_records, sidebar_date=daily_date),
        )
        archive_links.append(f'<li><a href="{BASE}/daily/{html_escape(daily_date)}/">{html_escape(daily_date)}</a> ({len(daily_records)})</li>')
        archive_rows.append(
            f"""<tr>
  <td><a href="{BASE}/daily/{html_escape(daily_date)}/">{html_escape(daily_date)}</a></td>
  <td>{html_escape(official_summary)}</td>
  <td>{journal_count}</td>
  <td>{working_count}</td>
  <td>{len(daily_records)}</td>
</tr>"""
        )

    journals = load_journals(DATA_DIR / "journals.yml")
    journals_by_id = {journal["id"]: journal for journal in journals}
    for journal_id, journal_records in by_journal.items():
        title = str(journal_records[0].get("journal") or journals_by_id.get(journal_id, {}).get("title") or journal_id)
        latest_journal_date = detected_date(journal_records[0]) if journal_records else None
        latest_journal_records = [record for record in journal_records if detected_date(record) == latest_journal_date] if latest_journal_date else []
        view_links = journal_view_links(journal_id, journal_records, today_records)
        body = f'<section class="section-head"><div><h2>{html_escape(title)}</h2><p>该期刊历史发现记录。</p></div></section>{view_links}{filter_toolbar(journal_records)}{scoped_shared_events("default", journal_records, records, [f"journal:{journal_id}"])}{FILTER_SCRIPT}'
        write_page(
            args.docs_dir / "journals" / journal_id / "index.html",
            page(title, records, body, active="journals", sidebar_records=latest_journal_records, sidebar_date=latest_journal_date),
        )
        recent = recent_records(journal_records, 7)
        recent_body = f'<section class="section-head"><div><h2>{html_escape(title)}：最近 7 天</h2><p>按该期刊最近有记录日期向前滚动 7 天。</p></div><p>{len(recent)} 篇</p></section>{view_links}{filter_toolbar(recent)}{paper_events(recent)}{FILTER_SCRIPT}'
        write_page(
            args.docs_dir / "journals" / journal_id / "recent7" / "index.html",
            page(f"{title} 最近 7 天", records, recent_body, active="journals", sidebar_records=latest_journal_records, sidebar_date=latest_journal_date),
        )

    for journal in journals:
        if journal["id"] in by_journal:
            continue
        title = str(journal.get("title") or journal["id"])
        body = f'<section class="section-head"><div><h2>{html_escape(title)}</h2><p>该期刊暂无有效论文记录。</p></div></section>{paper_events([])}'
        write_page(args.docs_dir / "journals" / journal["id"] / "index.html", page(title, records, body, active="journals"))
        write_page(
            args.docs_dir / "journals" / journal["id"] / "recent7" / "index.html",
            page(f"{title} 最近 7 天", records, body, active="journals"),
        )

    journal_rows = []
    for journal in journals:
        fields = ", ".join(field_label(field) for field in journal.get("fields", []))
        issn = journal.get("issn") or "待补充"
        publisher = journal.get("publisher") or "待补充"
        journal_rows.append(
            f"""<tr>
  <td><a href="{BASE}/journals/{html_escape(journal['id'])}/">{html_escape(journal.get('title'))}</a><div class="muted">{html_escape(journal.get('chinese_name'))}</div></td>
  <td>{html_escape(journal.get('short_name'))}</td>
  <td>{html_escape(fields)}</td>
  <td>{html_escape(issn)}</td>
  <td>{html_escape(publisher)}</td>
</tr>"""
        )
    journals_body = f"""<section class="section-head"><div><h2>监测期刊</h2><p>当前监测清单共 {len(journals)} 本期刊。优先级仅用于抓取频率，不在公开页面展示。</p></div></section>
<table class="journal-table"><thead><tr><th>期刊</th><th>缩写</th><th>领域</th><th>ISSN</th><th>出版社</th></tr></thead><tbody>{"".join(journal_rows)}</tbody></table>"""
    write_page(args.docs_dir / "journals" / "index.html", page("监测期刊", records, journals_body, active="journals"))

    for field, field_records in by_field.items():
        title = field_label(field)
        body = f'<section class="section-head"><div><h2>{html_escape(title)}</h2><p>该领域历史发现记录。</p></div></section>{filter_toolbar(field_records)}{scoped_shared_events("default", field_records, records, [f"field:{field}"])}{FILTER_SCRIPT}'
        write_page(args.docs_dir / "fields" / field / "index.html", page(title, records, body))

    for topic, topic_records in by_topic.items():
        title = topic_label(topic)
        note = "基于规则、AI 判定和人工确认的中国相关记录。" if topic == "china" else "基于标题、摘要和期刊信息生成的文章主题标签。"
        topic_links = topic_view_links(topic, topic_records, today_records)
        latest_topic_date = detected_date(topic_records[0]) if topic_records else None
        latest_topic_records = [record for record in topic_records if detected_date(record) == latest_topic_date] if latest_topic_date else []
        body = (
            china_topic_body(records, topic_records, today_records)
            if topic == "china"
            else f'<section class="section-head"><div><h2>{html_escape(title)}</h2><p>{html_escape(note)}</p></div><p>{len(topic_records)} 篇</p></section>{topic_links}{filter_toolbar(topic_records)}{scoped_shared_events("default", topic_records, records, [f"field:{topic}"])}{FILTER_SCRIPT}'
        )
        write_page(
            args.docs_dir / "topics" / topic / "index.html",
            page(title, records, body, active="china" if topic == "china" else "", sidebar_records=latest_topic_records, sidebar_date=latest_topic_date),
        )
        recent = recent_records(topic_records, 7)
        recent_body = (
            china_topic_body(records, recent, today_records)
            if topic == "china"
            else f'<section class="section-head"><div><h2>{html_escape(title)}：最近 7 天</h2><p>{html_escape(note)}</p></div><p>{len(recent)} 篇</p></section>{topic_links}{filter_toolbar(recent)}{paper_events(recent)}{FILTER_SCRIPT}'
        )
        write_page(
            args.docs_dir / "topics" / topic / "recent7" / "index.html",
            page(f"{title} 最近 7 天", records, recent_body, active="china" if topic == "china" else "", sidebar_records=latest_topic_records, sidebar_date=latest_topic_date),
        )

    write_page(
        args.docs_dir / "archive" / "index.html",
        archive_compatibility_page(),
    )
    write_lazy_indexes(args.docs_dir)
    print(f"rendered {len(records)} records into {args.docs_dir}")


if __name__ == "__main__":
    main()
