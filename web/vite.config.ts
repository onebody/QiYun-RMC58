import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import { configDefaults } from "vitest/config";

// Writes dist/sw.js from sw.js with the build's files and a cache name from
// their hash. FILES is the page itself: the public files, firmware/index.json
// (the versions the dialog offers) and the entry's static imports. FLASH_CODE
// is the code only the flasher loads (dynamic imports). INDEX is the parsed
// firmware/index.json, so the worker can find each version's files for the
// "Make firmware available offline" option. Build only: the dev server has no
// service worker.
function pwaPlugin(): Plugin {
  let root = ".";
  let outDir = "dist";
  const shell = new Set<string>(); // the entry chunks' static imports and their CSS
  return {
    name: "widscan58-sw",
    apply: "build",
    configResolved(c) {
      root = c.root;
      outDir = resolve(c.root, c.build.outDir);
    },
    generateBundle(_, bundle) {
      const add = (name: string) => {
        const c = bundle[name];
        if (!c || shell.has(name)) return;
        shell.add(name);
        if (c.type !== "chunk") return;
        c.imports.forEach(add);
        c.viteMetadata?.importedCss.forEach((f) => shell.add(f));
        c.viteMetadata?.importedAssets.forEach((f) => shell.add(f));
      };
      for (const c of Object.values(bundle)) if (c.type === "chunk" && c.isEntry) add(c.fileName);
    },
    closeBundle() {
      const walk = (d: string): string[] =>
        readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(d, e.name)) : [join(d, e.name)]));
      const all = walk(outDir)
        .map((f) => relative(outDir, f).split("\\").join("/"))
        .filter((f) => f !== "sw.js")
        .sort();
      const isPage = (f: string) =>
        f === "firmware/index.json" || (!f.startsWith("firmware/") && (!f.startsWith("assets/") || shell.has(f)));
      const hash = createHash("sha256");
      for (const f of all) hash.update(f).update(readFileSync(join(outDir, f)));
      const index = all.includes("firmware/index.json")
        ? JSON.stringify(JSON.parse(readFileSync(join(outDir, "firmware", "index.json"), "utf8")))
        : "null";
      const head =
        `const CACHE = "widscan58-${hash.digest("hex").slice(0, 16)}";\n` +
        `const FILES = ${JSON.stringify(all.filter(isPage))};\n` +
        `const FLASH_CODE = ${JSON.stringify(all.filter((f) => !isPage(f) && !f.startsWith("firmware/")))};\n` +
        `const INDEX = ${index};\n`;
      writeFileSync(join(outDir, "sw.js"), head + readFileSync(join(root, "sw.js"), "utf8"));
    },
  };
}

// Dev-only mock for /api/endpoints so the DeviceList dialog renders real cards
// during E2E without a real serve.mjs or any USB hardware. Enabled only when
// WIDSCAN_E2E_MOCK=1 is set; every other env is unaffected.
function devMockEndpointsPlugin(): Plugin | null {
  if (process.env.WIDSCAN_E2E_MOCK !== "1") return null;
  const MOCK_ENDPOINTS = [
    // WiFi relay group (transport=wifi, shown first)
    { id: "node:widscan58-52a1d8", kind: "remote", label: "widscan58-52a1d8", online: true, busy: false, auto: true, scannable: true },
    { id: "node:widscan58-99ffee", kind: "remote", label: "widscan58-99ffee (busy)", online: true, busy: true, auto: true, scannable: true },
    // USB serial group (transport=usb, shown second)
    { id: "serial:/dev/cu.usbmodem1301", kind: "serial", label: "cu.usbmodem1301", path: "/dev/cu.usbmodem1301", online: true, busy: false, auto: true, scannable: true },
    { id: "serial:/dev/cu.usbmodem1201", kind: "serial", label: "cu.usbmodem1201 (offline)", path: "/dev/cu.usbmodem1201", online: false, busy: false, auto: true, scannable: true },
    // UART group (also transport=usb)
    { id: "uart:/dev/serial0", kind: "uart", label: "serial0", online: true, busy: false, auto: false },
  ];
  return {
    name: "widscan58-dev-mock-endpoints",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url) return next();
        const path = req.url.split("?")[0];
        if (path === "/api/endpoints") {
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.setHeader("Cache-Control", "no-cache");
          res.end(JSON.stringify({ endpoints: MOCK_ENDPOINTS }));
          return;
        }
        next();
      });
    },
  };
}

// Relative base so the static build can be opened from any path (GitHub Pages, file host).
export default defineConfig({
  base: "./",
  plugins: [pwaPlugin(), devMockEndpointsPlugin()].filter(Boolean),
  test: {
    // Inherit Vitest's default exclude (**/node_modules/**, **/.git/**) so we stay
    // compatible with future Vitest versions; only add our local exclusions.
    exclude: [...configDefaults.exclude, "e2e/**", "**/*.mjs"],
  },
});
