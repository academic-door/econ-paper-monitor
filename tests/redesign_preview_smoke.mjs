import assert from "node:assert/strict";
import { chromium } from "playwright";

const root = process.env.PREVIEW_URL || "http://127.0.0.1:8777/";
const width = Number(process.env.VIEWPORT_WIDTH || 1440);
const mobile = width <= 480;
const browser = await chromium.launch({ headless: true });

const expectedNav = ["今日", "最近72小时", "中国研究", "工作论文", "期刊", "搜索"];
const paths = ["", "recent72/", "topics/china/", "working-papers/", "journals/", "search/"];

async function open(path) {
  const page = await browser.newPage({ viewport: { width, height: mobile ? 844 : 900 } });
  const url = new URL(path, root).href;
  const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  assert.equal(response?.status(), 200, `${url} did not return 200`);
  await page.waitForTimeout(650);
  return { page, url };
}

try {
  for (const path of paths) {
    const { page, url } = await open(path);
    assert.ok(await page.locator(".preview-banner").isVisible(), `${url} preview banner missing`);
    const robots = await page.locator('meta[name="robots"]').getAttribute("content");
    assert.match(robots || "", /noindex/);
    assert.match(robots || "", /nofollow/);
    assert.equal(await page.locator(".presence,.presence-cluster,.context-nav,.page-eyebrow").count(), 0, `${url} leaked production chrome`);

    const labels = await page.locator("#primary-nav a").allTextContents();
    assert.deepEqual(labels.map((x) => x.trim()), expectedNav, `${url} nav order`);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${url} overflow`);

    if (path) {
      const h1 = (await page.locator(".page-hero h1").innerText()).trim();
      const firstH2 = page.locator(".wrap h2").first();
      if (await firstH2.count()) {
        assert.notEqual((await firstH2.innerText()).trim(), h1, `${url} repeats H1 as first H2`);
      }
      assert.equal(await page.locator("details.more-filters").count(), 0, `${url} advanced internal filters still visible`);
      assert.equal(await page.locator(".pill.lag:visible").count(), 0, `${url} lag diagnostic visible`);
      assert.equal(await page.getByText("暂无 DOI", { exact: true }).count(), 0, `${url} missing DOI diagnostic visible`);
    }

    const detail = page.locator('a[href*="/paper/"]').first();
    if (await detail.count()) {
      const href = await detail.getAttribute("href");
      assert.ok(href?.startsWith("https://academic-door.github.io/econ-paper-monitor/paper/"), `${url} preview detail did not route to production detail: ${href}`);
    }
    await page.close();
  }

  {
    const { page } = await open("topics/china/");
    assert.equal((await page.locator(".page-hero h1").innerText()).trim(), "中国研究");
    const headings = await page.locator(".section-head h2").allTextContents();
    assert.ok(headings.some((x) => x.trim().startsWith("期刊论文")), "China journal heading not simplified");
    assert.ok(headings.some((x) => x.trim().startsWith("工作论文")), "China working heading not simplified");
    await page.close();
  }

  {
    const { page } = await open("working-papers/");
    const body = await page.locator("body").innerText();
    assert.ok(!body.includes("Quantifying financial repression through the lens of portfolio choice"), "research commentary leaked into Working Papers preview");
    assert.equal(await page.locator(".view-tabs + .stats:visible").count(), 0, "duplicated Working Papers stats still visible");
    await page.close();
  }

  {
    const { page } = await open("search/");
    const status = page.locator(".preview-result-status");
    assert.ok(await status.isVisible(), "search result status missing");
    const search = page.locator('[data-filter-role="search"]');
    await search.fill("china");
    await page.waitForTimeout(500);
    assert.equal(new URL(page.url()).searchParams.get("q"), "china", "search state not serialized to URL");
    const clear = page.locator("[data-preview-clear]");
    assert.ok(await clear.isVisible(), "clear-filter action missing");
    await clear.click();
    await page.waitForTimeout(350);
    assert.equal(new URL(page.url()).searchParams.get("q"), null, "clear did not reset URL");
    await page.close();
  }

  if (mobile) {
    const { page } = await open("journals/");
    const hiddenIssn = await page.locator(".journal-table td:nth-child(4)").first().evaluate((node) => getComputedStyle(node).display === "none");
    assert.equal(hiddenIssn, true, "mobile journal directory still exposes dense ISSN column");
    await page.close();
  }

  console.log(`Daily Door redesign preview smoke passed at ${width}px`);
} finally {
  await browser.close();
}
