#!/usr/bin/env node
/**
 * Headless TradingView lightweight-charts → PNG.
 *
 * Usage:
 *   node render.mjs <input.json> [output.png]
 *   cat input.json | node render.mjs - [output.png]
 *
 * Input JSON:
 *   {
 *     "candles": [{"time": 1710000000, "open":1,"high":2,"low":0.5,"close":1.5}, ...],
 *     "lines": [{"price": 1.2, "title": "Mark", "color": "#ffffff"}],
 *     "title": "BTC/USD LONG",
 *     "width": 900,
 *     "height": 480
 *   }
 *
 * Writes PNG to output path (default: chart.png) or stdout if output is "-".
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const __dirname = dirname(fileURLToPath(import.meta.url));
const LW_PATH = join(
  __dirname,
  'node_modules',
  'lightweight-charts',
  'dist',
  'lightweight-charts.standalone.production.js',
);

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  '/usr/bin/google-chrome-stable',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean);

function findChrome() {
  for (const p of CHROME_CANDIDATES) {
    if (existsSync(p)) return p;
  }
  return null;
}

function readInput(arg) {
  if (!arg || arg === '-') {
    return JSON.parse(readFileSync(0, 'utf8'));
  }
  return JSON.parse(readFileSync(resolve(arg), 'utf8'));
}

function buildHtml(payload, lwSource) {
  const width = Number(payload.width) || 900;
  const height = Number(payload.height) || 480;
  const dataJson = JSON.stringify({
    candles: payload.candles || [],
    lines: payload.lines || [],
    title: payload.title || '',
    width,
    height,
  });

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  html, body {
    margin: 0; padding: 0;
    background: #0f1115;
    overflow: hidden;
    width: ${width}px;
    height: ${height}px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  #wrap {
    position: relative;
    width: ${width}px;
    height: ${height}px;
    background: #0f1115;
  }
  #title {
    position: absolute;
    top: 8px;
    left: 12px;
    z-index: 5;
    color: #e8eaed;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.02em;
    pointer-events: none;
    text-shadow: 0 1px 2px rgba(0,0,0,0.8);
  }
  #chart {
    width: ${width}px;
    height: ${height}px;
  }
</style>
</head>
<body>
<div id="wrap">
  <div id="title"></div>
  <div id="chart"></div>
</div>
<script>${lwSource}</script>
<script>
window.__CHART_READY__ = false;
window.__CHART_ERROR__ = null;
(function () {
  try {
    var payload = ${dataJson};
    document.getElementById('title').textContent = payload.title || '';
    var el = document.getElementById('chart');
    var chart = LightweightCharts.createChart(el, {
      width: payload.width,
      height: payload.height,
      layout: {
        background: { type: 'solid', color: '#0f1115' },
        textColor: '#c5c8ce',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1a1e26' },
        horzLines: { color: '#1a1e26' },
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: '#2a2f3a',
        scaleMargins: { top: 0.12, bottom: 0.08 },
      },
      timeScale: {
        borderColor: '#2a2f3a',
        timeVisible: true,
        secondsVisible: false,
      },
      localization: { priceFormatter: function (p) {
        if (Math.abs(p) >= 1000) return p.toLocaleString('en-US', { maximumFractionDigits: 2 });
        if (Math.abs(p) >= 1) return p.toFixed(4);
        return p.toFixed(6);
      }},
    });

    var series = chart.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });

    var candles = (payload.candles || []).map(function (c) {
      return {
        time: Number(c.time),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      };
    }).filter(function (c) {
      return c.time && isFinite(c.open) && isFinite(c.high) && isFinite(c.low) && isFinite(c.close);
    });

    series.setData(candles);

    var lines = payload.lines || [];
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (ln == null || ln.price == null || !isFinite(Number(ln.price))) continue;
      series.createPriceLine({
        price: Number(ln.price),
        color: ln.color || '#ffffff',
        lineWidth: ln.lineWidth || 2,
        lineStyle: (ln.lineStyle != null) ? ln.lineStyle : LightweightCharts.LineStyle.Solid,
        axisLabelVisible: true,
        title: ln.title || '',
      });
    }

    // Auto-scale focuses on candle range; price lines remain visible even if off-scale.
    chart.timeScale().fitContent();

    // Mark ready on next frames so layout/paint settle.
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        window.__CHART_READY__ = true;
      });
    });
  } catch (err) {
    window.__CHART_ERROR__ = String(err && err.stack ? err.stack : err);
  }
})();
</script>
</body>
</html>`;
}

async function main() {
  const t0 = Date.now();
  const inArg = process.argv[2];
  const outArg = process.argv[3] || 'chart.png';

  if (!inArg) {
    console.error('Usage: node render.mjs <input.json|-> [output.png|-]');
    process.exit(2);
  }

  if (!existsSync(LW_PATH)) {
    console.error('Missing lightweight-charts at', LW_PATH);
    console.error('Run install in charts/ first.');
    process.exit(1);
  }

  const payload = readInput(inArg);
  if (!payload.candles || !payload.candles.length) {
    console.error('Input must include non-empty candles[]');
    process.exit(1);
  }

  const chrome = findChrome();
  if (!chrome) {
    console.error('No Chrome/Chromium found. Set CHROME_PATH.');
    process.exit(1);
  }

  const lwSource = readFileSync(LW_PATH, 'utf8');
  const html = buildHtml(payload, lwSource);
  const width = Number(payload.width) || 900;
  const height = Number(payload.height) || 480;

  const browser = await chromium.launch({
    executablePath: chrome,
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--hide-scrollbars',
    ],
  });

  try {
    const page = await browser.newPage({
      viewport: { width, height, deviceScaleFactor: 1 },
    });
    await page.setContent(html, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(
      () => window.__CHART_READY__ === true || window.__CHART_ERROR__ != null,
      null,
      { timeout: 8000 },
    );
    const err = await page.evaluate(() => window.__CHART_ERROR__);
    if (err) {
      throw new Error('Chart render error: ' + err);
    }
    // Small settle for price-line labels / canvas paint.
    await page.waitForTimeout(80);
    const png = await page.locator('#wrap').screenshot({ type: 'png' });

    if (outArg === '-') {
      process.stdout.write(png);
    } else {
      const outPath = resolve(outArg);
      writeFileSync(outPath, png);
      const elapsed = Date.now() - t0;
      console.error(
        JSON.stringify({
          ok: true,
          path: outPath,
          bytes: png.length,
          candles: payload.candles.length,
          lines: (payload.lines || []).length,
          elapsed_ms: elapsed,
        }),
      );
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
