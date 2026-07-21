const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

async function main() {
  const baseUrl = (process.argv[2] || "http://127.0.0.1:8010").replace(/\/$/, "");
  const username = process.argv[3];
  const password = process.argv[4];
  const outputDir = path.resolve(process.argv[5] || "tmp/visual-acceptance");
  if (!username || !password) throw new Error("usage: node tests/visual_acceptance.js <base-url> <username> <password> [output-dir]");
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_BROWSER_PATH || undefined });
  const viewports = [
    { name: "mobile", width: 375, height: 812 },
    { name: "tablet", width: 768, height: 1024 },
    { name: "laptop", width: 1024, height: 768 },
    { name: "desktop", width: 1440, height: 1000 },
  ];
  let checks = 0;
  try {
    for (const viewport of viewports) {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
      const page = await context.newPage();
      await page.goto(`${baseUrl}/login`);
      await page.locator("input[name=username]").fill(username);
      await page.locator("input[name=password]").fill(password);
      await Promise.all([page.waitForURL(/\/workspace/), page.locator("button[type=submit]").click()]);
      const projectHref = await page.locator('a[href^="/projects/"]').first().getAttribute("href");
      if (!projectHref) throw new Error("演示数据中没有可检查的项目详情链接。");
      const pages = [
        ["dashboard", "/workspace"], ["projects", "/workspace/projects"], ["calendar", "/workspace/calendar"],
        ["archives", "/workspace/archives"], ["settings", "/workspace/settings"], ["detail", projectHref],
        ["editor", `${projectHref}/dynamic-editor`],
      ];
      for (const [name, route] of pages) {
        await page.goto(`${baseUrl}${route}`);
        await page.waitForLoadState("networkidle");
        const metrics = await page.evaluate(() => ({ width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth, bodyWidth: document.body.scrollWidth }));
        if (metrics.scrollWidth > metrics.width + 1 || metrics.bodyWidth > metrics.width + 1) {
          throw new Error(`${viewport.name}/${name} 出现页面级横向溢出: ${JSON.stringify(metrics)}`);
        }
        await page.screenshot({ path: path.join(outputDir, `${viewport.name}-${name}.png`), fullPage: true });
        checks += 2;
      }
      await page.goto(`${baseUrl}${projectHref}/dynamic-editor`);
      const before = await page.locator(".section-builder").count();
      await page.locator("[data-add-section]").click();
      const after = await page.locator(".section-builder").count();
      if (after !== before + 1) throw new Error(`${viewport.name} 编辑器未能添加标签页。`);
      await page.locator(".section-builder").last().locator('input[aria-label="标签页名称"]').fill("验收预览标签");
      if (!(await page.locator("[data-editor-preview]").getByText("验收预览标签").isVisible())) throw new Error(`${viewport.name} 编辑器实时预览未更新。`);
      checks += 2;
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ ok: true, checks, screenshots: 28, outputDir }));
}

main().catch(error => { console.error(error.stack || error); process.exit(1); });
