// P1 回归：把基线 .widscan58 样本喂回原始 Console，验证「回放 → 解析 → 计数」这条链
// 在历史字节流上仍然跑得通。GPL-3.0-or-later。
//
//   node tests/regression/replay-check.mjs [--url http://localhost:5199/]
//         [--chrome "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
//         [--fixture tests/fixtures/baseline-golden-2026-11-04.widscan58]
//
// 不带 --url 时自己起一个 `vite preview`（跑的是 `web/dist`，即构建产物本身），结束必关；
// 只关自己拉起的那个进程。
//
// 判据是"能恢复"，不是"零坏帧"：夹具里 `corrupted_frame_then_good_one` 是**故意**造坏的，
// 回放必须报出 crcErrors，并且在那之后继续出帧。断言 crcErrors==0 会把这条能力测成反向。
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

// puppeteer-core 装在 web/ 下（原始 Console 自己的依赖），这里按 web/package.json
// 的位置解析它，不在 tests/ 再装一份——两份 puppeteer 迟早版本分叉。
const require = createRequire(new URL("../../web/package.json", import.meta.url));
const puppeteer = await import(pathToFileURL(require.resolve("puppeteer-core")).href);

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../..");
const arg = (name, def) => {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : def;
};
const chrome = arg("--chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
const fixture = resolve(repo, arg("--fixture", "tests/fixtures/baseline-golden-2026-11-04.widscan58"));
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
};

if (!existsSync(fixture)) {
  console.error(`fixture 不存在：${fixture}\n先跑 node tests/fixtures/make-baseline-recording.mjs`);
  process.exit(2);
}

let server = null;
let base = arg("--url", "");
if (!base) {
  // 用 localhost 而不是 127.0.0.1：vite preview 在 macOS 上绑的是 ::1（它自己打印的也是
  // localhost），写死 127.0.0.1 会连不上——这是一次实测撞出来的，不是猜的。
  base = "http://localhost:5199/";
  server = spawn("npx", ["vite", "preview", "--port", "5199", "--strictPort"], {
    cwd: resolve(repo, "web"),
    stdio: ["ignore", "inherit", "inherit"],
  });
  for (let i = 0; i < 60; i++) {
    const ok = await fetch(base).then((r) => r.ok, () => false);
    if (ok) break;
    await new Promise((r) => setTimeout(r, 250));
    if (i === 59) {
      server.kill();
      throw new Error(`vite preview 在 ${base} 上没有起来（15 s 超时）`);
    }
  }
}

try {
  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: true,
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 1000 });
    const consoleErrors = [];
    page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
    page.on("pageerror", (e) => consoleErrors.push(String(e)));
    await page.goto(base, { waitUntil: "networkidle2" });

    // 顺序是实测出来的：`window.widscan58` 这个 hook 在 connect() 里才挂上
    // （main.ts:512），未连接时页面故意没有它；所以先喂文件、再断言 hook。
    // puppeteer 25 的 ElementHandle 上传方法叫 uploadFile，不叫 setInputFiles。
    const input = await page.waitForSelector('input[accept=".widscan58"]', { hidden: false });
    await input.uploadFile(fixture);
    // 夹具只有 2 s 节拍，播完就 `replay finished` 自动断开；等 3 s 再取快照必然什么都取不到
    // （这是第一次跑撞出来的）。改成每 200 ms 采一次、留下计数最多的那一次。
    const polls = [];
    for (let i = 0; i < 15; i++) {
      await new Promise((r) => setTimeout(r, 200));
      polls.push(
        await page.evaluate(() => {
          const { app, store } = window.widscan58 ?? {};
          const dev = app?.device;
          return {
            connected: !!dev,
            transport: dev?.transport?.name ?? null,
            stats: dev?.stats?.() ?? null,
            logs: (store?.log ?? []).filter((l) => l.dir === "error").map((l) => l.text),
          };
        }),
      );
    }
    const best = polls.reduce((a, b) =>
      (b.stats?.frames ? Object.values(b.stats.frames).reduce((x, y) => x + y, 0) : 0) >
      (a.stats?.frames ? Object.values(a.stats.frames).reduce((x, y) => x + y, 0) : 0)
        ? b
        : a,
    polls[0]);
    const sawReplay = polls.some((p) => p.transport?.startsWith("Replay"));
    const endLog = await page.evaluate(() =>
      (window.widscan58?.store?.log ?? []).some((l) => /replay finished/.test(l.text)),
    );

    check("page exposes app hook", await page.evaluate(() => !!window.widscan58));
    check("replay transport attached", sawReplay, best.transport);
    check("replay ran to end", endLog, "store.log 里有 `replay finished`");
    const s = { stats: best.stats, logs: best.logs };
    s.frames = best.stats?.frames;
    const total = Object.values(s.frames ?? {}).reduce((a, b) => a + b, 0);
    check("replay connected", best.connected === false ? sawReplay : best.connected, best.transport);
    check("replay decoded frames", total > 0, `total=${total} byType=${JSON.stringify(s.frames)}`);
    check("replay counted bytes", (s.stats?.bytes ?? 0) > 1000, JSON.stringify(s.stats));
    check(
      "corrupt frame reported then recovered",
      (s.stats?.crcErrors ?? 0) >= 1 && total >= 3,
      `crcErrors=${s.stats?.crcErrors} resync=${s.stats?.resyncBytes} frames=${total}`,
    );
    check("no app error log during replay", s.logs.length === 0, s.logs.join(" | "));
    check("no console errors", consoleErrors.length === 0, consoleErrors.join(" | "));
  } finally {
    await browser.close();
  }
} finally {
  server?.kill();
}

const failed = results.filter((r) => !r.ok);
console.log(`${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
