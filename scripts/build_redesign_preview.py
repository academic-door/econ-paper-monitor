#!/usr/bin/env python3
"""Build the isolated Daily Door redesign preview bundle.

This does not mutate production source data or production-root HTML. It copies a
bounded set of already-rendered public surfaces into a preview subtree, injects
preview-only presentation/interaction changes, and keeps paper-detail links on
current production so the preview does not duplicate the full static detail
corpus.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

PREVIEW_SLUG = "daily-door-redesign-v1"
PRODUCTION_BASE = "https://academic-door.github.io/econ-paper-monitor"
DEFAULT_PREVIEW_BASE = f"{PRODUCTION_BASE}/preview/{PREVIEW_SLUG}"

PREVIEW_STYLE = r"""
<style id="daily-door-redesign-preview-style">
:root{--muted:#5f615e!important}
.preview-banner{position:relative;z-index:20;background:#173f55;color:#fff;padding:.55rem 1rem;text-align:center;font:600 .78rem/1.4 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:.04em}
.preview-banner a{color:#fff;text-decoration:underline;text-underline-offset:2px}
.site-header{top:0}
.context-nav,.presence,.presence-cluster,.page-eyebrow,.more-filters,.empty.home-note{display:none!important}
.page-hero{display:block!important;padding:3.2rem 0 1.6rem!important;border-bottom:0!important}
.page-hero h1{font-size:clamp(2.8rem,6vw,5.2rem)!important;margin:0!important}
.page-hero p{max-width:680px!important;margin-top:.8rem!important;color:var(--muted)!important}
.preview-hero-meta{display:block;margin-top:.8rem;font:500 .78rem/1.4 var(--mono);color:var(--blue)}
.wrap{padding-top:.35rem!important}
.section-head{border-bottom:0!important;padding-top:1.75rem!important}
.section-head.split-section{margin-top:2rem!important}
.stats{margin:.8rem 0 1.1rem!important}
.event{border-bottom:0!important;padding-top:1.4rem!important;padding-bottom:1.55rem!important}
.meta-block{gap:.3rem!important}
.pill.lag{display:none!important}
.preview-export-actions{display:flex;gap:.55rem;flex-wrap:wrap;margin:.6rem 0 1rem}
.preview-export-actions a{border:1px solid var(--line);padding:.35rem .55rem;font-size:.76rem;color:var(--muted);background:rgba(251,250,247,.55)}
.preview-result-status{display:flex;align-items:center;justify-content:space-between;gap:.75rem;min-height:32px;margin:-.6rem 0 .9rem;color:var(--muted);font-size:.78rem}
.preview-result-status button{border:0;background:transparent;color:var(--blue);padding:.25rem .1rem;font:inherit;cursor:pointer}
.preview-active-filters{display:flex;gap:.35rem;flex-wrap:wrap;margin:.15rem 0 .75rem}
.preview-active-filters span{border:1px solid var(--line);padding:.18rem .4rem;font-size:.72rem;color:var(--muted);background:rgba(251,250,247,.55)}
.preview-working .view-tabs + .stats{display:none!important}
.preview-china .pill.china{display:none!important}
.preview-search .stats,.preview-recent72 .stats,.preview-china .stats{border-top:0!important}
.paper-entry,.paper-entry *{animation-duration:.18s!important}
@media(max-width:720px){
  .page-hero{padding:2.2rem 0 1.15rem!important}
  .page-hero h1{font-size:clamp(2.45rem,13vw,4.2rem)!important}
  .stats{grid-template-columns:repeat(2,minmax(0,1fr))!important}
  .preview-journals .journal-table th:nth-child(2),
  .preview-journals .journal-table td:nth-child(2),
  .preview-journals .journal-table th:nth-child(4),
  .preview-journals .journal-table td:nth-child(4),
  .preview-journals .journal-table th:nth-child(5),
  .preview-journals .journal-table td:nth-child(5){display:none!important}
  .preview-journals .journal-table{display:table!important;overflow:visible!important;width:100%!important}
  .preview-result-status{align-items:flex-start;flex-direction:column}
}
@media(prefers-reduced-motion:reduce){
  .secondary-motion .event,.paper-entry,.paper-entry *{opacity:1!important;transform:none!important;transition:none!important;animation:none!important}
}
</style>
"""

PREVIEW_SCRIPT = r"""
<script id="daily-door-redesign-preview-script">
(() => {
  const PROD = 'https://academic-door.github.io/econ-paper-monitor';
  const navOrder = ['今日','最近72小时','中国研究','工作论文','期刊','搜索'];
  document.querySelectorAll('nav.nav').forEach((nav) => {
    const links = [...nav.querySelectorAll('a')];
    const byLabel = new Map(links.map((a) => [a.textContent.trim(), a]));
    navOrder.forEach((label) => { if (byLabel.has(label)) nav.append(byLabel.get(label)); });
  });

  document.querySelectorAll('.presence,.presence-cluster,.context-nav,.page-eyebrow').forEach((node) => node.remove());

  const path = location.pathname;
  const body = document.body;
  const configs = [
    ['/recent72/', 'preview-recent72', '最近72小时', '过去三天新发现的经济学研究。'],
    ['/topics/china/', 'preview-china', '中国研究', '聚焦中国数据、制度、市场与研究对象。'],
    ['/working-papers/', 'preview-working', null, '浏览工作论文与机构研究。'],
    ['/journals/', 'preview-journals', '期刊', '按期刊浏览 Daily Door 收录的研究。'],
    ['/search/', 'preview-search', '搜索', '搜索标题、作者、期刊、来源与主题。'],
  ];
  const config = configs.find(([needle]) => path.includes(needle));
  if (config) {
    body.classList.add(config[1]);
    const hero = document.querySelector('.page-hero');
    const h1 = hero?.querySelector('h1');
    const lede = hero?.querySelector('p:last-of-type');
    if (config[2] && h1) h1.textContent = config[2];
    if (lede) lede.textContent = config[3];

    const firstSection = document.querySelector('.wrap > .section-head');
    if (firstSection) {
      const side = [...firstSection.children].find((node) => node.tagName === 'P');
      const value = side?.textContent?.trim();
      if (value && hero && !body.classList.contains('preview-china')) {
        const meta = document.createElement('span');
        meta.className = 'preview-hero-meta';
        meta.textContent = value;
        hero.querySelector('div')?.append(meta);
      }
      firstSection.remove();
    }
  }

  if (body.classList.contains('preview-working')) {
    const h1 = document.querySelector('.page-hero h1');
    if (h1 && /全部工作论文/.test(h1.textContent)) h1.textContent = '工作论文';
  }

  if (body.classList.contains('preview-china')) {
    document.querySelectorAll('.section-head h2').forEach((h2) => {
      h2.innerHTML = h2.innerHTML.replace('与中国相关：期刊论文', '期刊论文').replace('与中国相关：工作论文', '工作论文');
    });
  }

  if (body.classList.contains('preview-recent72')) {
    const stats = document.querySelector('.stats');
    if (stats) {
      const utilities = [...stats.children].filter((item) => ['RIS','BibTeX'].includes(item.querySelector('strong')?.textContent?.trim()));
      if (utilities.length) {
        const row = document.createElement('div');
        row.className = 'preview-export-actions';
        utilities.forEach((item) => {
          const link = item.matches('a') ? item : item.querySelector('a');
          if (link?.href) {
            const a = document.createElement('a');
            a.href = link.href;
            a.textContent = '导出 ' + (item.querySelector('strong')?.textContent?.trim() || '文献');
            row.append(a);
          }
          item.remove();
        });
        stats.after(row);
      }
    }
  }

  const cleanCard = (card) => {
    card.querySelectorAll('.pill.lag').forEach((node) => node.remove());
    card.querySelectorAll('.pill').forEach((node) => {
      if (node.textContent.trim().startsWith('本站首次发现')) node.remove();
      if (body.classList.contains('preview-china') && node.textContent.trim() === '与中国相关') node.remove();
    });
    card.querySelectorAll('.doi').forEach((node) => {
      const value = node.textContent.trim();
      if (value === '暂无 DOI') node.remove();
      if (value === '文章链接') node.textContent = '原文';
    });
    card.querySelectorAll('.meta-label').forEach((node) => {
      if (node.textContent.trim() === '链接/DOI') node.textContent = '链接';
    });
    const timeBox = card.querySelector('.time')?.parentElement;
    if (timeBox) timeBox.setAttribute('title', '本站首次发现');
  };

  document.querySelectorAll('.event').forEach(cleanCard);
  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      if (node.matches?.('.event')) cleanCard(node);
      node.querySelectorAll?.('.event').forEach(cleanCard);
    }));
  }).observe(document.getElementById('main-content') || document.body, {subtree:true, childList:true});

  document.querySelectorAll('details.more-filters').forEach((details) => {
    details.querySelectorAll('select').forEach((select) => {
      select.value = '';
      select.dispatchEvent(new Event('change', {bubbles:true}));
    });
    details.remove();
  });

  const toolbars = [...document.querySelectorAll('.toolbar[data-filter-scope]')];
  if (toolbars.length === 1) {
    const toolbar = toolbars[0];
    const status = document.createElement('div');
    status.className = 'preview-result-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    status.innerHTML = '<span data-preview-result-count></span><button type="button" data-preview-clear hidden>清除筛选</button>';
    toolbar.after(status);
    const chips = document.createElement('div');
    chips.className = 'preview-active-filters';
    status.after(chips);

    const search = toolbar.querySelector('[data-filter-role="search"]');
    const journal = toolbar.querySelector('[data-filter-role="journal"]');
    const field = toolbar.querySelector('[data-filter-role="field"]');
    const china = toolbar.querySelector('[data-filter-role="china"]');
    const clear = status.querySelector('[data-preview-clear]');
    const countNode = status.querySelector('[data-preview-result-count]');

    const active = () => {
      const parts = [];
      if (search?.value.trim()) parts.push('关键词：' + search.value.trim());
      if (journal?.value) parts.push(journal.options[journal.selectedIndex]?.textContent?.trim() || '来源');
      if (field?.value) parts.push(field.options[field.selectedIndex]?.textContent?.trim() || '主题');
      if (china?.getAttribute('aria-pressed') === 'true') parts.push('中国研究');
      return parts;
    };
    const update = () => {
      const parts = active();
      chips.replaceChildren(...parts.map((label) => {
        const span = document.createElement('span');
        span.textContent = label;
        return span;
      }));
      clear.hidden = parts.length === 0;
      const visible = [...document.querySelectorAll('.event')].filter((node) => !node.hidden && getComputedStyle(node).display !== 'none').length;
      const lazyButton = document.querySelector('.lazy-start');
      const totalMatch = lazyButton?.textContent?.match(/(\d+)/);
      if (!parts.length && totalMatch) countNode.textContent = totalMatch[1] + ' 条记录';
      else countNode.textContent = visible ? '当前显示 ' + visible + ' 条' : '无匹配结果';
    };
    let restoringHistory = false;
    const urlForControls = () => {
      const params = new URLSearchParams();
      if (search?.value.trim()) params.set('q', search.value.trim());
      if (journal?.value) params.set('journal', journal.value);
      if (field?.value) params.set('field', field.value);
      if (china?.getAttribute('aria-pressed') === 'true') params.set('china', '1');
      return location.pathname + (params.toString() ? '?' + params.toString() : '') + location.hash;
    };
    const syncUrl = () => {
      if (restoringHistory) return;
      const next = urlForControls();
      const current = location.pathname + location.search + location.hash;
      if (next !== current) history.pushState(null, '', next);
    };
    const applyUrlState = () => {
      const params = new URLSearchParams(location.search);
      restoringHistory = true;
      if (search) search.value = params.get('q') || '';
      if (journal) journal.value = params.get('journal') || '';
      if (field) field.value = params.get('field') || '';
      if (china) {
        const wanted = params.get('china') === '1';
        const current = china.getAttribute('aria-pressed') === 'true';
        if (wanted !== current) china.click();
      }
      search?.dispatchEvent(new Event('input', {bubbles:true}));
      journal?.dispatchEvent(new Event('change', {bubbles:true}));
      field?.dispatchEvent(new Event('change', {bubbles:true}));
      setTimeout(() => {
        restoringHistory = false;
        update();
      }, 220);
    };
    const changed = () => setTimeout(() => { update(); syncUrl(); }, 220);
    toolbar.addEventListener('input', changed);
    toolbar.addEventListener('change', changed);
    toolbar.addEventListener('click', changed);
    window.addEventListener('popstate', applyUrlState);

    clear.addEventListener('click', () => {
      if (search) search.value = '';
      if (journal) journal.value = '';
      if (field) field.value = '';
      if (china?.getAttribute('aria-pressed') === 'true') china.click();
      search?.dispatchEvent(new Event('input', {bubbles:true}));
      journal?.dispatchEvent(new Event('change', {bubbles:true}));
      field?.dispatchEvent(new Event('change', {bubbles:true}));
      const cleared = location.pathname + location.hash;
      if (location.pathname + location.search + location.hash !== cleared) history.pushState(null, '', cleared);
      setTimeout(update, 220);
    });

    applyUrlState();

    const resultRoot = document.querySelector('[data-lazy-list]');
    if (resultRoot) {
      new MutationObserver(update).observe(resultRoot, {subtree:true, childList:true, attributes:true, attributeFilter:['hidden']});
    }
    setTimeout(update, 350);
  }

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const nav = document.getElementById('primary-nav');
    const menu = document.querySelector('.menu[aria-controls="primary-nav"]');
    if (nav?.classList.contains('open')) {
      nav.classList.remove('open');
      menu?.setAttribute('aria-expanded', 'false');
      menu?.setAttribute('aria-label', '打开导航');
      menu?.focus();
    }
  });

  document.querySelectorAll('a[href*="/paper/"]').forEach((a) => {
    if (a.href.includes('/preview/')) {
      const match = a.href.match(/\/paper\/(.+)$/);
      if (match) a.href = PROD + '/paper/' + match[1];
    }
  });
})();
</script>
"""


def copy_path(source_root: Path, output_root: Path, relative: str) -> None:
    src = source_root / relative
    if not src.exists():
        return
    dst = output_root / relative
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def preview_url(preview_base: str, relative_html: Path) -> str:
    rel = relative_html.as_posix()
    if rel == "index.html":
        return preview_base.rstrip("/") + "/"
    if rel.endswith("/index.html"):
        rel = rel[: -len("index.html")]
    return preview_base.rstrip("/") + "/" + rel


def strip_presence_script(document: str) -> str:
    pattern = re.compile(r"<script>(?:(?!</script>).)*epd_presence_client(?:(?!</script>).)*</script>", re.S)
    return pattern.sub("", document)


def process_html(path: Path, output_root: Path, preview_base: str) -> None:
    document = path.read_text(encoding="utf-8")
    route = preview_url(preview_base, path.relative_to(output_root))

    document = strip_presence_script(document)
    document = re.sub(
        r'\s*<script[^>]+src="https://static\.cloudflareinsights\.com/beacon\.min\.js"[^>]*></script>',
        "",
        document,
        flags=re.I,
    )

    robots = '<meta name="robots" content="noindex,nofollow,noarchive">'
    if re.search(r'<meta\s+name="robots"[^>]*>', document, re.I):
        document = re.sub(r'<meta\s+name="robots"[^>]*>', robots, document, count=1, flags=re.I)
    else:
        document = document.replace("<head>", "<head>\n  " + robots, 1)

    canonical = f'<link rel="canonical" href="{route}">'
    if re.search(r'<link\s+rel="canonical"[^>]*>', document, re.I):
        document = re.sub(r'<link\s+rel="canonical"[^>]*>', canonical, document, count=1, flags=re.I)
    else:
        document = document.replace("</head>", "  " + canonical + "\n</head>", 1)
    document = re.sub(r'(<meta\s+property="og:url"\s+content=")[^"]*(")', rf'\1{route}\2', document, flags=re.I)

    document = re.sub(
        r'href="(?:(?:\.\./)+|\./)?paper/',
        f'href="{PRODUCTION_BASE}/paper/',
        document,
    )
    document = re.sub(
        r'data-lazy-base="[^"]*"',
        f'data-lazy-base="{PRODUCTION_BASE}"',
        document,
    )

    banner = (
        '<div class="preview-banner" role="status">'
        'Daily Door Redesign Preview v1 · 非正式页面 · '
        '<a href="https://github.com/academic-door/econ-paper-monitor/issues/329">Review gate #329</a>'
        '</div>'
    )
    document = re.sub(r'(<body(?:\s[^>]*)?>)', r'\1\n' + banner, document, count=1, flags=re.I)
    document = document.replace("</head>", PREVIEW_STYLE + "\n</head>", 1)
    document = document.replace("</body>", PREVIEW_SCRIPT + "\n</body>", 1)
    path.write_text(document, encoding="utf-8")


def copy_lazy_datasets(source_root: Path, output_root: Path) -> int:
    dataset_ids: set[str] = set()
    for html_path in output_root.rglob("*.html"):
        text = html_path.read_text(encoding="utf-8")
        dataset_ids.update(re.findall(r"paper-index/([0-9a-f]{16})/manifest\.json", text))
    for dataset_id in sorted(dataset_ids):
        copy_path(source_root, output_root, f"paper-index/{dataset_id}")
    return len(dataset_ids)


def build(source_root: Path, output_root: Path, preview_base: str) -> None:
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    for relative in (
        "index.html",
        "recent72/index.html",
        "topics/china/index.html",
        "search/index.html",
    ):
        copy_path(source_root, output_root, relative)

    for relative in ("working-papers", "journals", "assets", "exports"):
        copy_path(source_root, output_root, relative)

    datasets = copy_lazy_datasets(source_root, output_root)

    for html_path in output_root.rglob("*.html"):
        process_html(html_path, output_root, preview_base)

    marker = output_root / "PREVIEW_BUILD.txt"
    marker.write_text(
        f"Daily Door Redesign Preview v1\nsource={source_root}\ndatasets={datasets}\n",
        encoding="utf-8",
    )
    print(f"Preview bundle: html={sum(1 for _ in output_root.rglob('*.html'))} datasets={datasets} output={output_root}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("docs"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview-base", default=DEFAULT_PREVIEW_BASE)
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve(), args.preview_base)


if __name__ == "__main__":
    main()
