import assert from "node:assert/strict";
import { chromium } from "playwright";

const root = process.env.SITE_ROOT_URL || process.env.DAILY_VNEXT_URL || "https://academic-door.github.io/econ-paper-monitor/";
const dailyVnext = new URL("daily-vnext/", root).href;
const urls = [root, dailyVnext];
const isLocal = ["127.0.0.1", "localhost"].includes(new URL(root).hostname);
const viewportWidth = Number(process.env.VIEWPORT_WIDTH || 0);
const isMobile = viewportWidth > 0 && viewportWidth <= 480;
const pageOptions = viewportWidth > 0
  ? { viewport: { width: viewportWidth, height: isMobile ? 844 : 900 } }
  : undefined;

async function visibleEntries(page, selector = '.paper-entry') {
  return page.locator(`${selector}:not([hidden])`).count();
}


async function assertNoPseudoDiscoveryTime(page, url) {
  assert.equal(
    await page.locator('.time').filter({ hasText: /^\s*监测\s*$/ }).count(),
    0,
    `${url} rendered 监测 as a timeline time`,
  );
  const bodyText = await page.locator('body').innerText();
  assert.ok(
    !/本站首次发现[^\n]{0,48}监测/.test(bodyText),
    `${url} rendered a pseudo-time inside first-discovery metadata`,
  );
}

async function assertSearchCountConsistency(page, url) {
  const summary = (await page.locator('.section-head > p').first().innerText()).match(/(\d+)\s*条/);
  const start = page.locator('.lazy-start').first();
  if (!(await start.count())) return;
  const browse = (await start.innerText()).match(/浏览全部\s*(\d+)\s*篇/);
  assert.ok(summary && browse, `${url} search totals are not parseable`);
  assert.equal(Number(summary[1]), Number(browse[1]), `${url} search totals disagree`);
}

async function assertUniqueSourceFilterLabels(page, url) {
  const select = page.locator('.toolbar[data-filter-scope="search"] [data-filter-role="journal"]').first();
  if (!(await select.count())) return;
  const options = await select.locator('option').evaluateAll((nodes) =>
    nodes
      .map((node) => ({ label: (node.textContent || '').trim(), value: node.value }))
      .filter((item) => item.value && item.label),
  );
  const valuesByLabel = new Map();
  for (const option of options) {
    if (!valuesByLabel.has(option.label)) valuesByLabel.set(option.label, new Set());
    valuesByLabel.get(option.label).add(option.value);
  }
  const conflicts = [...valuesByLabel.entries()]
    .filter(([, values]) => values.size > 1)
    .map(([label, values]) => `${label} => ${[...values].join(', ')}`);
  assert.deepEqual(conflicts, [], `${url} has duplicate source labels with conflicting identities`);
}

async function assertNoStaleTodayBackfill(page, url) {
  const bodyText = await page.locator('body').innerText();
  const daily = bodyText.match(/DAILY DOOR\s*\/\s*(\d{4}-\d{2}-\d{2})/);
  if (!daily) return;
  const target = Date.parse(`${daily[1]}T00:00:00Z`);
  const cutoff = target - (2 * 24 * 60 * 60 * 1000);
  const details = page.locator('.paper-entry details.paper-details');
  for (let index = 0; index < await details.count(); index += 1) {
    const text = (await details.nth(index).textContent()) || '';
    const official = text.match(/(?:官方在线|官方发布)\s+(\d{4}-\d{2}-\d{2})/);
    if (!official) continue;
    assert.ok(
      Date.parse(`${official[1]}T00:00:00Z`) >= cutoff,
      `${url} leaked strongly dated stale catalogue backfill into Today: ${official[1]}`,
    );
  }
}

async function assertNavigationLinksHealthy(page, url) {
  const hrefs = await page.locator('.site-header .nav a, .context-nav a').evaluateAll((nodes) =>
    [...new Set(nodes.map((node) => node.getAttribute('href')).filter(Boolean))],
  );
  for (const href of hrefs) {
    const target = new URL(href, page.url());
    if (target.origin !== new URL(page.url()).origin) continue;
    const response = await page.request.get(target.href);
    assert.ok(response.status() < 400, `${url} navigation target failed: ${target.href} => ${response.status()}`);
  }
}

async function scopedResultCount(page, scope, url) {
  const lazy = page.locator(`[data-lazy-list][data-lazy-scope="${scope}"]`).first();
  if (await lazy.count()) {
    const manifestUrl = await lazy.getAttribute('data-lazy-manifest');
    assert.ok(manifestUrl, `${url} missing lazy manifest for ${scope}`);
    const response = await page.request.get(new URL(manifestUrl, page.url()).href);
    assert.equal(response.status(), 200, `${url} manifest failed for ${scope}`);
    const manifest = await response.json();
    return Number(manifest.count || 0);
  }
  return page.locator(`.event[data-event-scope="${scope}"]`).count();
}

async function assertChinaCountConsistency(page, url) {
  const totalText = await page.locator('.section-head').first().locator(':scope > p').innerText();
  const totalMatch = totalText.match(/(\d+)\s*篇/);
  assert.ok(totalMatch, `${url} China headline total is not parseable`);

  const journalStat = Number(await page.locator('.stats a[href="#china-journals"] strong').innerText());
  const workingStat = Number(await page.locator('.stats a[href="#china-working"] strong').innerText());
  const journalResults = await scopedResultCount(page, 'china-journal', url);
  const workingResults = await scopedResultCount(page, 'china-working', url);

  assert.equal(Number(totalMatch[1]), journalResults + workingResults, `${url} China headline total disagrees with result sets`);
  assert.equal(journalStat, journalResults, `${url} China journal stat disagrees with result set`);
  assert.equal(workingStat, workingResults, `${url} China working-paper stat disagrees with result set`);
}

async function assertNoFilterLazyState(page, url) {
  const lazyList = page.locator('[data-lazy-list]').first();
  if (!(await lazyList.count())) return;
  const manifestUrl = await lazyList.getAttribute('data-lazy-manifest');
  const manifestResponse = await page.request.get(new URL(manifestUrl, page.url()).href);
  const manifest = await manifestResponse.json();
  if (!manifest.count) return;
  await page.waitForTimeout(1200);
  assert.ok(await page.locator('.event').count() >= 1, `${url} empty state despite records`);
  assert.equal(await page.locator('[data-lazy-empty]:visible').count(), 0, `${url} empty state visible without filters`);
}

async function checkPage(browser, url) {
  const errors = [];
  const page = await browser.newPage(pageOptions);
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(2400);
  assert.equal(errors.length, 0, `${url} page errors: ${errors.join(" | ")}`);
  assert.ok(await page.locator('.hero h1').isVisible(), `${url} Hero title is not visible`);
  assert.ok(await page.locator('.hero-lede').isVisible(), `${url} Hero lede is not visible`);
  assert.ok((await page.locator('.hero-total').count()) >= 1);
  await assertNoPseudoDiscoveryTime(page, url);
  await assertNoStaleTodayBackfill(page, url);
  if (url === root) await assertNavigationLinksHealthy(page, url);
  assert.ok(await page.locator('.nav a[href*="feed.xml"], .footer-links a[href*="feed.xml"]').count() >= 1, `${url} RSS link missing`);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${url} has horizontal overflow`);
  const entries = page.locator('.paper-entry');
  const total = await entries.count();
  assert.ok(total >= 0);

  const search = page.locator('input.search').last();
  const china = page.locator('.filter[data-filter="china"]').last();
  assert.ok(await search.isVisible(), `${url} paper search is not visible`);
  assert.ok(await china.isVisible(), `${url} China filter is not visible`);
  assert.equal(await visibleEntries(page), total, `${url} default paper filter`);
  if (await china.isDisabled()) {
    const label = await china.getAttribute('aria-label');
    assert.match(label, /0 项/, `${url} disabled China filter should report zero items`);
  } else {
    await china.click();
    await page.waitForTimeout(250);
    assert.equal(await visibleEntries(page), await page.locator('.paper-entry[data-china="true"]').count(), `${url} China filter`);
  }
  await page.locator('.filter[data-filter="all"]').click();
  const searchable = total > 0
    ? await entries.first().getAttribute('data-search')
    : null;
  if (searchable) {
    await search.fill(searchable.split(/\s+/)[0]);
    await page.waitForTimeout(250);
    assert.ok(await visibleEntries(page) >= 1, `${url} search returned no result`);
  }
  await page.close();
}

async function checkSecondaryPages(browser) {
  const paths = ["classic/", "recent72/", "topics/china/", "archive/", "search/", "journals/", "working-papers/"];
  for (const path of paths) {
    const url = new URL(path, root).href;
    const page = await browser.newPage(pageOptions);
    const errors = [];
    const indexRequests = [];
    let indexBytes = 0;
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("request", (request) => {
      if (request.url().includes("/paper-index/")) indexRequests.push(request.url());
    });
    page.on("response", async (response) => {
      if (response.url().includes("/paper-index/")) {
        try {
          indexBytes += (await response.body()).length;
        } catch {
          // response body is optional for request accounting
        }
      }
    });
    const response = await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(500);
    assert.equal(response?.status(), 200, `${url} did not return 200`);
    assert.equal(errors.length, 0, `${url} page errors: ${errors.join(" | ")}`);
    if (path === "classic/") {
      assert.ok(await page.locator('.sidebar').isVisible(), `${url} classic sidebar is missing`);
    } else {
      assert.ok(await page.locator('.site-header').isVisible(), `${url} vNext header is missing`);
      assert.equal(await page.locator('.sidebar').count(), 0, `${url} legacy sidebar leaked`);
      assert.ok(await page.locator('.secondary-page').isVisible(), `${url} secondary shell is missing`);
      assert.ok(await page.locator('.nav a[href*="feed.xml"], .footer-links a[href*="feed.xml"]').count() >= 1, `${url} RSS link missing`);
      await assertNoFilterLazyState(page, url);
      await assertNoPseudoDiscoveryTime(page, url);
    }
    assert.equal(indexRequests.some((requestUrl) => requestUrl.endsWith("/paper-index.json")), false, `${url} requested the legacy full index`);
    if (path === "topics/china/") {
      await assertChinaCountConsistency(page, url);
    }
    if (path === "search/") {
      await assertSearchCountConsistency(page, url);
      await assertUniqueSourceFilterLabels(page, url);
      assert.equal(indexRequests.length, 0, `${url} downloaded search data before user interaction`);
      assert.equal(indexBytes, 0, `${url} downloaded search bytes before user interaction`);
      const initialEntries = await page.locator('.event').count();
      assert.ok(initialEntries <= 20, `${url} initial DOM exceeds fallback budget: ${initialEntries}`);
      const browse = page.locator('.lazy-start').first();
      if (await browse.count()) {
        await browse.click();
        await page.waitForFunction((count) => document.querySelectorAll('.event').length > count, initialEntries);
        assert.ok(indexRequests.some((requestUrl) => requestUrl.endsWith("/manifest.json")), `${url} did not load its search manifest on demand`);
        assert.ok(indexRequests.some((requestUrl) => /\/shards\/\d{4}\.json$/.test(requestUrl)), `${url} did not load a result shard on demand`);
        if (isLocal) {
          const firstSearch = await page.locator('.event').first().getAttribute('data-search');
          const searchInput = page.locator('.toolbar[data-filter-scope="search"] [data-filter-role="search"]');
          if (firstSearch) {
            await searchInput.fill(firstSearch.split(/\s+/)[0]);
            await page.waitForTimeout(1800);
            assert.ok(indexRequests.some((requestUrl) => /\/route\/\d{3}\.json$/.test(requestUrl)), `${url} did not load a routed bucket on search`);
            assert.ok(indexRequests.some((requestUrl) => /\/shards\/\d{4}\.json$/.test(requestUrl)), `${url} did not load routed content shards on search`);
            assert.ok(await page.locator('.event').count() >= 1, `${url} search returned no result`);
            assert.ok(indexBytes < 1_000_000, `${url} routed search transferred too many bytes: ${indexBytes}`);
            const lazyCardHtml = await page.locator('.event').evaluateAll((nodes) => nodes.map((node) => node.innerHTML));
            assert.ok(lazyCardHtml.every((html) => html.includes('https://doi.org/') || html.includes('暂无 DOI')), `${url} lazy card missing DOI status`);
            const more = page.locator('.lazy-more').first();
            if (await more.count()) {
              const entriesBeforeMore = await page.locator('.event').count();
              let moreExhausted = false;
              for (let moreClicks = 0; moreClicks < 12; moreClicks += 1) {
                const moreBtn = page.locator('.lazy-more').first();
                if (!(await moreBtn.count()) || !(await moreBtn.isVisible())) {
                  moreExhausted = true;
                  break;
                }
                await moreBtn.click();
                try {
                  await page.waitForFunction((count) => document.querySelectorAll('.event').length > count, entriesBeforeMore, { timeout: 6000 });
                  break;
                } catch (e) {
                  await page.waitForTimeout(400);
                }
              }
              const entriesAfterMore = await page.locator('.event').count();
              if (!(entriesAfterMore > entriesBeforeMore)) {
                const moreVisible = await page.locator('.lazy-more').first().isVisible().catch(() => false);
                moreExhausted = moreExhausted || !moreVisible;
              }
              assert.ok(entriesAfterMore > entriesBeforeMore || moreExhausted, `${url} load more did not render additional entries or exhaust`);
              if (moreExhausted) {
                assert.equal(await page.locator('.lazy-more').first().isVisible().catch(() => false), false, `${url} exhausted load more stayed visible`);
              }
              assert.ok(indexBytes < 2_000_000, `${url} load-more transfer too high: ${indexBytes}`);
            }
          }
        }
      }
    }
    if (path === "working-papers/") {
      assert.ok(indexRequests.some((requestUrl) => requestUrl.endsWith("/manifest.json")), `${url} did not load its scoped manifest`);
      assert.ok(indexRequests.length <= 3, `${url} loaded too many initial index files: ${indexRequests.length}`);
      const before = await page.locator('.event').count();
      const more = page.locator('.lazy-more').first();
      if (await more.count()) {
        await more.click();
        await page.waitForFunction((count) => document.querySelectorAll('.event').length > count, before);
      }
    }
    if (path === "recent72/") {
      const lazyList = page.locator('[data-lazy-list]').first();
      if (await lazyList.count()) {
        const manifestUrl = await lazyList.getAttribute('data-lazy-manifest');
        const manifestResponse = await page.request.get(new URL(manifestUrl, page.url()).href);
        const manifest = await manifestResponse.json();
        if (manifest.count > 0) {
          await page.waitForTimeout(1500);
          assert.ok(await page.locator('.event').count() >= 1, `${url} recent72 empty state despite records`);
          assert.equal(await page.locator('[data-lazy-empty]').isVisible(), false, `${url} recent72 empty state visible without filters`);
        }
      }
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${url} has horizontal overflow`);
    if (isMobile && path !== "classic/") {
      const summary = page.locator('.toolbar .more-filters summary').first();
      if (await summary.count()) {
        await summary.click();
        await page.waitForTimeout(300);
        assert.ok(await page.locator('.toolbar .more-filters .more-filters-row').first().isVisible(), `${url} advanced filters did not open at 390px`);
        assert.ok(await page.locator('.toolbar .more-filters .more-filters-row select').count() >= 1, `${url} advanced filters have no readable selects`);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${url} advanced filters caused overflow`);
        await page.keyboard.press('Escape');
        await page.waitForTimeout(200);
        assert.equal(await page.locator('.toolbar .more-filters[open]').count(), 0, `${url} advanced filters did not close on Escape`);
      }
    }
    await page.close();
  }
  if (isLocal) {
    const journalsPage = await browser.newPage(pageOptions);
    await journalsPage.goto(new URL("journals/", root).href, { waitUntil: "networkidle", timeout: 60000 });
    const journalLinks = await journalsPage.locator('.journal-table a[href*="/journals/"]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('href')).filter(Boolean));
    await journalsPage.close();
    for (const href of journalLinks.slice(0, 8)) {
      const journalUrl = new URL(href, new URL("journals/", root).href).href;
      const journalPage = await browser.newPage(pageOptions);
      const response = await journalPage.goto(journalUrl, { waitUntil: "networkidle", timeout: 60000 });
      if (response?.status() === 200 && await journalPage.locator('[data-lazy-list]').count()) {
        await assertNoFilterLazyState(journalPage, journalUrl);
        await journalPage.close();
        break;
      }
      await journalPage.close();
    }
  }
  if (isLocal) {
    let sharedTopicFound = false;
    for (const topic of ["agriculture", "development", "finance", "macro", "labor", "trade", "china"]) {
      const topicUrl = new URL("topics/" + topic + "/", root).href;
      const topicPage = await browser.newPage(pageOptions);
      const errors = [];
      topicPage.on("pageerror", (error) => errors.push(String(error)));
      const response = await topicPage.goto(topicUrl, { waitUntil: "networkidle", timeout: 60000 });
      const shared = await topicPage.locator('.lazy-list[data-lazy-filter]').count();
      if (response?.status() === 200 && shared > 0) {
        await topicPage.waitForTimeout(1500);
        assert.equal(errors.length, 0, `${topicUrl} page errors: ${errors.join(" | ")}`);
        assert.ok(await topicPage.locator('.event').count() >= 1, `${topicUrl} shared topic page rendered no events`);
        const counter = topicPage.locator('[data-filter-counter]').first();
        if (await counter.count()) {
          const counterText = await counter.innerText();
          assert.ok(!/当前显示 0 篇/.test(counterText), `${topicUrl} shared counter shows zero despite events`);
        }
        const sharedMore = topicPage.locator('.lazy-more').first();
        if (await sharedMore.count()) {
          const before = await topicPage.locator('.event').count();
          await sharedMore.click();
          await topicPage.waitForTimeout(1500);
          assert.ok(await topicPage.locator('.event').count() >= before, `${topicUrl} load more did not keep events stable`);
        }
        if (isMobile) {
          assert.ok(await topicPage.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${topicUrl} has horizontal overflow`);
        }
        sharedTopicFound = true;
        await topicPage.close();
        break;
      }
      await topicPage.close();
    }
    assert.ok(sharedTopicFound, "no shared topic page found in local fixtures");
  }
  const feedPage = await browser.newPage();
  const feed = await feedPage.request.get(new URL("feed.xml", root).href);
  assert.equal(feed.status(), 200, "feed.xml did not return 200");
  await feedPage.close();
}

async function checkDetailPage(browser) {
  const listing = await browser.newPage();
  await listing.goto(new URL("recent72/", root).href, { waitUntil: "networkidle", timeout: 60000 });
  const detailHref = await listing.locator('a[href*="/paper/"]').first().getAttribute('href');
  assert.ok(detailHref, "Recent72 does not expose a static detail-page link");
  assert.ok(!detailHref.includes("paper.html?key="), "Recent72 still exposes the legacy detail route as canonical");
  const detailUrl = new URL(detailHref, listing.url()).href;
  await listing.close();

  const page = await browser.newPage(pageOptions);
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  const response = await page.goto(detailUrl, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForFunction(() => !document.querySelector('#paperRoot')?.classList.contains('detail-loading'));
  assert.equal(response?.status(), 200, `${detailUrl} did not return 200`);
  assert.equal(errors.length, 0, `${detailUrl} page errors: ${errors.join(" | ")}`);
  assert.ok(await page.locator('.detail-page h1').isVisible(), `${detailUrl} detail title is missing`);
  assert.notEqual((await page.locator('.detail-page h1').innerText()).trim(), '正在载入论文详情');
  assert.ok(await page.locator('.site-header').isVisible(), `${detailUrl} vNext header is missing`);
  assert.equal(await page.locator('.sidebar').count(), 0, `${detailUrl} legacy sidebar leaked`);
  assert.equal(await page.locator('a[href*="archive/"]').count(), 0, `${detailUrl} archive navigation leaked`);
  const detailLinks = await page.locator('.detail-links').innerHTML();
  assert.ok(detailLinks.includes('https://doi.org/') || detailLinks.includes('暂无 DOI'), `${detailUrl} detail missing DOI status`);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${detailUrl} has horizontal overflow`);
  await assertNoPseudoDiscoveryTime(page, detailUrl);
  await page.close();
}

async function checkGsapFallback(browser) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.route("**/gsap.min.js", (route) => route.abort());
  await page.route("**/ScrollTrigger.min.js", (route) => route.abort());
  await page.route("**/Flip.min.js", (route) => route.abort());
  await page.goto(root, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(500);
  assert.ok(await page.locator('.hero h1').isVisible(), "Homepage fallback hid Hero title");
  assert.ok(await page.locator('.hero-lede').isVisible(), "Homepage fallback hid Hero lede");
  assert.equal(errors.length, 0, `GSAP fallback errors: ${errors.join(" | ")}`);
  await page.close();
}

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROME_EXECUTABLE || undefined,
});
try {
  for (const url of urls) await checkPage(browser, url);
  await checkSecondaryPages(browser);
  await checkDetailPage(browser);
  await checkGsapFallback(browser);
  console.log(`Public Product Audit passed at ${viewportWidth || "default"}px for ${urls.join(" and ")}`);
} finally {
  await browser.close();
}
