// Chrome/Chromium executable discovery and validation.
//
// Resolution priority (highest first):
//   1. --chrome <path>    CLI flag  (parsed by headless.mjs, passed in)
//   2. CHROME_BIN         environment variable
//   3. auto-detect        per-platform search below
//   4. error              exit non-zero with a useful message
//
// This module is pure logic + safe spawn calls (shell: false, args as array).
// It does NOT actually launch Puppeteer — callers do that with the returned path.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Test seam — tests override these per-function with Object.assign(..., { f: stub })
// to simulate arbitrary file systems and spawnSync outcomes. Production code never touches them.
export const _internals = {
  fsExistsSync: (p) => fs.existsSync(p),
  fsAccessSync: (p, m) => fs.accessSync(p, m),
  cpSpawnSync: (exe, args, opts) => spawnSync(exe, args, opts),
  osHomedir: () => os.homedir(),
  pathDelimiter: path.delimiter,
  pathJoin: (...parts) => path.join(...parts),
  platformDescriptor: Object.getOwnPropertyDescriptor(process, "platform"),
};

// ----- CLI arg parsing ----------------------------------------------------

/**
 * Parse known flags from argv. Returns { values, unknown, help }.
 *
 * Supported forms:
 *   --chrome /path             (space-separated)
 *   --chrome=/path             (equals-separated)
 *   --sim --bridge ws://...    (any boolean/string flag without a registered value key
 *                               appears in `unknown` so callers can detect typos)
 */
export function parseCliArgs(argv, { extraKnown = {} } = {}) {
  const known = {
    "--chrome": "string",
    "--sim": "boolean",
    "--help": "boolean",
    ...extraKnown,
  };
  const values = {};
  const unknown = [];
  let i = 0;
  while (i < argv.length) {
    const a = argv[i];
    if (a === "--help" || a === "-h") {
      values["--help"] = true;
      i++;
      continue;
    }
    if (a.startsWith("--")) {
      const [flag, eqVal] = a.split("=", 2);
      const spec = known[flag];
      if (!spec) {
        unknown.push(a);
        i++;
        continue;
      }
      if (spec === "boolean") {
        values[flag] = true;
        i++;
        continue;
      }
      // string flag
      if (eqVal !== undefined) {
        values[flag] = eqVal;
        i++;
      } else {
        const next = argv[i + 1];
        if (next === undefined || next.startsWith("--")) {
          throw new Error(`--chrome requires a value. Use: --chrome /path/to/chrome`);
        }
        values[flag] = next;
        i += 2;
      }
    } else {
      unknown.push(a);
      i++;
    }
  }
  return { values, unknown, help: !!values["--help"] };
}

export const HELP_TEXT = [
  "Usage: node e2e/headless.mjs [options]",
  "",
  "Options:",
  "  --chrome <path>     Path to Chrome/Chromium executable",
  "  --chrome=<path>     Same as above (equals form)",
  "  --sim               Use built-in simulator instead of hardware",
  "  --bridge <url>      WebSocket bridge URL (default ws://127.0.0.1:5809)",
  "  --url <url>         Dev server URL (default http://localhost:5173/)",
  "  --out <dir>         Output directory (default e2e-out)",
  "  --help, -h          Show this help",
  "",
  "Environment:",
  "  CHROME_BIN          Path to Chrome/Chromium (overrides auto-detect, overridden by --chrome)",
  "",
  "Chrome auto-detect checks known locations for your OS plus PATH lookups.",
].join("\n");

// ----- Auto-detect --------------------------------------------------------

function isExecutable(p) {
  try {
    _internals.fsAccessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Look up a command on PATH. Equivalent to `which` on POSIX, `where` on Windows,
 * but implemented with Node's fs so it works identically everywhere.
 */
function which(cmd) {
  const dirs = process.env.PATH?.split(_internals.pathDelimiter) ?? [];
  const suffixes = process.platform === "win32"
    // Browser installers ship as .exe; .com is the only legacy executable worth
    // keeping. .bat/.cmd/.ps1 are shell scripts — we intentionally drop them
    // because (a) spawnSync with shell: false can't run them, and (b) browsers
    // never ship as batch files.
    ? [".EXE", ".COM"]
    : [""];
  for (const d of dirs) {
    for (const suf of suffixes) {
      const p = _internals.pathJoin(d, cmd + suf);
      if (_internals.fsExistsSync(p) && isExecutable(p)) return p;
    }
  }
  return null;
}

function tryPaths(candidates) {
  for (const p of candidates) {
    if (_internals.fsExistsSync(p) && isExecutable(p)) return p;
  }
  return null;
}

function findOnMacOS() {
  const home = _internals.osHomedir();
  const bundles = [
    ["/Applications/Google Chrome.app", "Google Chrome"],
    [`${home}/Applications/Google Chrome.app`, "Google Chrome"],
    ["/Applications/Chromium.app", "Chromium"],
    [`${home}/Applications/Chromium.app`, "Chromium"],
    ["/Applications/Microsoft Edge.app", "Microsoft Edge"],
  ];
  const paths = bundles.map(([bundle, name]) =>
    _internals.pathJoin(bundle, "Contents", "MacOS", name)
  );
  return tryPaths(paths) || which("google-chrome") || which("chromium");
}

function findOnLinux() {
  // PATH first (user-installed / snap / flatpak may place any of these there)
  const pathLookups = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"];
  for (const cmd of pathLookups) {
    const p = which(cmd);
    if (p) return p;
  }
  // Then known absolute locations (Debian/RPM/Arch/Fedora common paths)
  const absCandidates = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    "/usr/lib/chromium-browser/chromium-browser",
    "/var/lib/flatpak/app/com.google.Chrome/current/active/export/bin/com.google.Chrome",
    "/var/lib/flatpak/app/org.chromium.Chromium/current/active/export/bin/org.chromium.Chromium",
  ];
  return tryPaths(absCandidates);
}

function findOnWindows() {
  // PATH first
  const pathLookups = ["chrome.exe", "chromium.exe", "msedge.exe"];
  for (const cmd of pathLookups) {
    const p = which(cmd);
    if (p) return p;
  }
  // Common install dirs
  const progFiles = process.env["ProgramFiles"] || "C:\\Program Files";
  const progFilesX86 = process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
  const localAppData = process.env["LocalAppData"] || _internals.pathJoin(_internals.osHomedir(), "AppData", "Local");
  const absCandidates = [
    _internals.pathJoin(progFiles, "Google", "Chrome", "Application", "chrome.exe"),
    _internals.pathJoin(progFilesX86, "Google", "Chrome", "Application", "chrome.exe"),
    _internals.pathJoin(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
    _internals.pathJoin(progFiles, "Chromium", "Application", "chrome.exe"),
    _internals.pathJoin(progFilesX86, "Chromium", "Application", "chrome.exe"),
    _internals.pathJoin(progFiles, "Microsoft", "Edge", "Application", "msedge.exe"),
  ];
  return tryPaths(absCandidates);
}

export function autoDetectChrome() {
  switch (process.platform) {
    case "darwin": return findOnMacOS();
    case "linux": return findOnLinux();
    case "win32": return findOnWindows();
    default: return findOnLinux() || findOnMacOS();
  }
}

// ----- Resolver: priority + validation ------------------------------------

/**
 * Run Chrome's --version flag and return the version string (e.g. "Google Chrome 131.0.6778.85").
 * Returns { ok: true, version } on success, { ok: false, error } otherwise.
 *
 * Uses spawnSync with shell: false and a 5-second timeout — safe for paths with spaces.
 */
export function getChromeVersion(executable, { timeoutMs = 5000 } = {}) {
  let result;
  try {
    result = _internals.cpSpawnSync(executable, ["--version"], {
      shell: false,
      timeout: timeoutMs,
      encoding: "utf-8",
      maxBuffer: 1024 * 1024,
    });
  } catch (err) {
    return { ok: false, error: `spawn failed: ${err.code || err.message}` };
  }
  if (result.error) {
    return { ok: false, error: result.error.code === "ENOENT" ? "file not found" : result.error.message };
  }
  if (result.status !== 0) {
    return { ok: false, error: `exit code ${result.status}: ${(result.stderr || result.stdout || "").trim()}` };
  }
  const v = (result.stdout || "").trim();
  if (!v) return { ok: false, error: "--version returned empty output" };
  return { ok: true, version: v };
}

export function validateChromeExecutable(executable) {
  if (!executable) return { ok: false, error: "path is empty" };
  if (!_internals.fsExistsSync(executable)) return { ok: false, error: `path does not exist: ${executable}` };
  if (!isExecutable(executable)) return { ok: false, error: `path exists but is not executable: ${executable}` };
  const v = getChromeVersion(executable);
  if (!v.ok) return { ok: false, error: `--version check failed: ${v.error}` };
  return { ok: true, version: v.version };
}

/**
 * Resolve Chrome executable using the documented priority chain.
 *
 * @param {{ cliPath?: string, allowAutoFallback?: boolean }} options
 * @returns {{ path?: string, source: "cli" | "env" | "auto" | "none", version?: string, error?: string, tried: string[] }}
 */
export function resolveChromeExecutable({ cliPath = undefined, allowAutoFallback = true } = {}) {
  const tried = [];

  // 1. CLI flag (highest)
  if (cliPath !== undefined && cliPath !== null && cliPath.trim() !== "") {
    tried.push(`--chrome ${cliPath}`);
    const v = validateChromeExecutable(cliPath);
    if (v.ok) return { path: cliPath, source: "cli", version: v.version, tried };
    return { path: undefined, source: "none", error: `CLI path invalid (${v.error})`, tried };
  }

  // 2. Environment variable
  const envPath = process.env.CHROME_BIN;
  if (envPath && envPath.trim() !== "") {
    tried.push(`CHROME_BIN=${envPath}`);
    const v = validateChromeExecutable(envPath);
    if (v.ok) return { path: envPath, source: "env", version: v.version, tried };
    return { path: undefined, source: "none", error: `CHROME_BIN invalid (${v.error})`, tried };
  }

  // 3. Auto-detect
  if (allowAutoFallback) {
    tried.push("auto-detect");
    const found = autoDetectChrome();
    if (found) {
      const v = validateChromeExecutable(found);
      if (v.ok) return { path: found, source: "auto", version: v.version, tried };
      return { path: undefined, source: "none", error: `auto-detect found ${found} but validation failed (${v.error})`, tried };
    }
    return { path: undefined, source: "none", error: "Chrome executable not found on this system", tried };
  }

  return { path: undefined, source: "none", error: "no Chrome path provided and auto-detect disabled", tried };
}

/** Human-readable diagnostic dump for the "not found" case. */
export function formatChromeNotFound(resolution) {
  const platform = process.platform;
  const arch = process.arch;
  const home = _internals.osHomedir();
  let candidates = [];
  if (platform === "darwin") {
    candidates = [
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      `${home}/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`,
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
      `${home}/Applications/Chromium.app/Contents/MacOS/Chromium`,
    ];
  } else if (platform === "linux") {
    candidates = [
      "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
      "/usr/bin/google-chrome", "/usr/bin/chromium-browser",
    ];
  } else if (platform === "win32") {
    const pf = process.env["ProgramFiles"] || "C:\\Program Files";
    const localAppData = process.env["LocalAppData"] || _internals.pathJoin(_internals.osHomedir(), "AppData", "Local");
    candidates = [
      _internals.pathJoin(pf, "Google", "Chrome", "Application", "chrome.exe"),
      _internals.pathJoin(process.env["ProgramFiles(x86)"] || pf, "Google", "Chrome", "Application", "chrome.exe"),
      _internals.pathJoin(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
    ];
  }
  const checked = [
    process.env.CHROME_BIN ? `CHROME_BIN=${process.env.CHROME_BIN}` : "(CHROME_BIN not set)",
    ...candidates.map((c) => `- ${c}`),
  ];
  return [
    "",
    "[ERROR] Chrome executable not found.",
    "",
    `Platform:  ${platform} (${arch})`,
    "",
    "Checked:",
    ...checked,
    "",
    "Specify Chrome manually:",
    "  node e2e/headless.mjs --chrome \"/path/to/Google Chrome\"",
    "",
    "Or set environment variable:",
    '  export CHROME_BIN="/path/to/Google Chrome"',
    "",
    "See also: docs/engineering/08-web-e2e-chrome-cross-platform.md",
    "",
  ].join("\n");
}
