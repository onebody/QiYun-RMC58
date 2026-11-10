// Unit tests for chrome-resolver.mjs.
// Run with:  node --test e2e/test/chrome-resolver.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";

import {
  parseCliArgs,
  resolveChromeExecutable,
  autoDetectChrome,
  getChromeVersion,
  validateChromeExecutable,
  formatChromeNotFound,
  _internals,
} from "../chrome-resolver.mjs";

// ----- helpers ------------------------------------------------------------

// Snapshot + restore _internals around each test block.
function patchInternals(patchObj) {
  const prev = { ..._internals };
  Object.assign(_internals, patchObj);
  return () => { Object.assign(_internals, prev); };
}

// Override process.platform for one test block.
function withPlatform(p, fn) {
  const desc = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { value: p, writable: false, configurable: true });
  try { return fn(); } finally {
    if (desc) Object.defineProperty(process, "platform", desc);
    else delete process.platform;
  }
}

function withEnvOverrides(obj, fn) {
  const prev = {};
  for (const [k, v] of Object.entries(obj)) {
    prev[k] = k in process.env ? process.env[k] : undefined;
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  try { return fn(); } finally {
    for (const [k, v] of Object.entries(prev)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

// =========================================================================
// parseCliArgs
// =========================================================================

test("parseCliArgs: --chrome /path", () => {
  const r = parseCliArgs(["--chrome", "/opt/chrome"]);
  assert.equal(r.values["--chrome"], "/opt/chrome");
});

test("parseCliArgs: --chrome=/path (equals form)", () => {
  const r = parseCliArgs(["--chrome=/opt/chrome"]);
  assert.equal(r.values["--chrome"], "/opt/chrome");
});

test("parseCliArgs: path with spaces (both forms)", () => {
  const a = parseCliArgs(["--chrome=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]);
  assert.equal(a.values["--chrome"], "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
  const b = parseCliArgs(["--chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]);
  assert.equal(b.values["--chrome"], "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
});

test("parseCliArgs: --chrome requires a value", () => {
  assert.throws(() => parseCliArgs(["--chrome"]), /requires a value/);
  assert.throws(() => parseCliArgs(["--chrome", "--sim"]), /requires a value/);
});

test("parseCliArgs: --sim boolean", () => {
  assert.equal(parseCliArgs(["--sim"]).values["--sim"], true);
});

test("parseCliArgs: --help / -h", () => {
  assert.equal(parseCliArgs(["--help"]).help, true);
  assert.equal(parseCliArgs(["-h"]).help, true);
});

test("parseCliArgs: unknown flag recorded", () => {
  assert.deepEqual(parseCliArgs(["--weird"]).unknown, ["--weird"]);
});

// =========================================================================
// resolver priority chain + CLI/env/auto interaction
// =========================================================================

test("resolver: CLI takes priority over CHROME_BIN", () => {
  const restore = patchInternals({
    fsExistsSync: (p) => p === "/cli/chrome" || p === "/env/chrome",
    fsAccessSync: (_p) => undefined,
    cpSpawnSync: (exe) => ({ status: 0, stdout: (exe === "/cli/chrome" ? "Chrome CLI" : "Chrome ENV") + "\n", stderr: "", error: null }),
  });
  try {
    withEnvOverrides({ CHROME_BIN: "/env/chrome" }, () => {
      const r = resolveChromeExecutable({ cliPath: "/cli/chrome" });
      assert.equal(r.source, "cli");
      assert.equal(r.path, "/cli/chrome");
    });
  } finally { restore(); }
});

test("resolver: CHROME_BIN takes priority over auto-detect", () => {
  const restore = patchInternals({
    fsExistsSync: (p) => p === "/env/chrome",
    fsAccessSync: (_p) => undefined,
    cpSpawnSync: () => ({ status: 0, stdout: "Chrome ENV\n", stderr: "", error: null }),
  });
  try {
    withEnvOverrides({ CHROME_BIN: "/env/chrome", PATH: "" }, () => {
      const r = resolveChromeExecutable();
      assert.equal(r.source, "env");
      assert.equal(r.path, "/env/chrome");
    });
  } finally { restore(); }
});

test("resolver: auto-detect on darwin finds system Chrome", () => {
  const restore = patchInternals({
    fsExistsSync: (p) => p === "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    fsAccessSync: (_p) => undefined,
    cpSpawnSync: () => ({ status: 0, stdout: "Chrome Auto\n", stderr: "", error: null }),
  });
  try {
    withPlatform("darwin", () => {
      withEnvOverrides({ PATH: "", CHROME_BIN: undefined }, () => {
        const r = resolveChromeExecutable();
        assert.equal(r.source, "auto");
        assert.equal(r.path, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
      });
    });
  } finally { restore(); }
});

test("resolver: CLI path invalid returns none, does NOT silently fall back to auto", () => {
  const restore = patchInternals({
    fsExistsSync: () => false,
  });
  try {
    withPlatform("linux", () => {
      withEnvOverrides({ PATH: "", CHROME_BIN: undefined }, () => {
        const r = resolveChromeExecutable({ cliPath: "/no/such/chrome" });
        assert.equal(r.source, "none");
        assert.equal(r.path, undefined);
        assert.ok(r.error);
        assert.doesNotMatch(r.error, /auto-detect/i);
      });
    });
  } finally { restore(); }
});

test("resolver: CHROME_BIN invalid returns none, no silent fallback", () => {
  const restore = patchInternals({ fsExistsSync: () => false });
  try {
    withEnvOverrides({ CHROME_BIN: "/no/such/chrome", PATH: "" }, () => {
      const r = resolveChromeExecutable();
      assert.equal(r.source, "none");
      assert.match(r.error, /invalid|not exist/i);
    });
  } finally { restore(); }
});

test("resolver: nothing found at all on clean linux", () => {
  const restore = patchInternals({ fsExistsSync: () => false });
  try {
    withPlatform("linux", () => {
      withEnvOverrides({ PATH: "", CHROME_BIN: undefined }, () => {
        const r = resolveChromeExecutable();
        assert.equal(r.source, "none");
        assert.match(r.error, /not found/i);
      });
    });
  } finally { restore(); }
});

test("resolver: nothing found + allowAutoFallback=false", () => {
  const restore = patchInternals({ fsExistsSync: () => false });
  try {
    withPlatform("linux", () => {
      withEnvOverrides({ PATH: "", CHROME_BIN: undefined }, () => {
        const r = resolveChromeExecutable({ allowAutoFallback: false });
        assert.equal(r.source, "none");
        assert.match(r.error, /disabled/i);
      });
    });
  } finally { restore(); }
});

// =========================================================================
// autoDetectChrome per platform
// =========================================================================

test("autoDetect: darwin — system Google Chrome", () => {
  const p = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const restore = patchInternals({ fsExistsSync: (x) => x === p, fsAccessSync: () => undefined });
  try {
    withPlatform("darwin", () => assert.equal(autoDetectChrome(), p));
  } finally { restore(); }
});

test("autoDetect: darwin — Chromium fallback", () => {
  const p = "/Applications/Chromium.app/Contents/MacOS/Chromium";
  const restore = patchInternals({ fsExistsSync: (x) => x === p, fsAccessSync: () => undefined });
  try {
    withPlatform("darwin", () => assert.equal(autoDetectChrome(), p));
  } finally { restore(); }
});

test("autoDetect: darwin — nothing returns null", () => {
  const restore = patchInternals({ fsExistsSync: () => false });
  try {
    withPlatform("darwin", () => assert.equal(autoDetectChrome(), null));
  } finally { restore(); }
});

test("autoDetect: linux — PATH lookup google-chrome-stable", () => {
  const p = "/usr/bin/google-chrome-stable";
  const restore = patchInternals({
    fsExistsSync: (x) => x === p,
    fsAccessSync: () => undefined,
    pathDelimiter: ":",
  });
  try {
    withPlatform("linux", () => {
      withEnvOverrides({ PATH: "/usr/bin" }, () => {
        assert.equal(autoDetectChrome(), p);
      });
    });
  } finally { restore(); }
});

test("autoDetect: linux — PATH empty, falls to absolute /usr/bin/google-chrome", () => {
  const p = "/usr/bin/google-chrome";
  const restore = patchInternals({
    fsExistsSync: (x) => x === p,
    fsAccessSync: () => undefined,
    pathDelimiter: ":",
  });
  try {
    withPlatform("linux", () => {
      withEnvOverrides({ PATH: "" }, () => assert.equal(autoDetectChrome(), p));
    });
  } finally { restore(); }
});

test("autoDetect: linux — chromium-browser absolute candidate", () => {
  const p = "/usr/bin/chromium-browser";
  const restore = patchInternals({
    fsExistsSync: (x) => x === p,
    fsAccessSync: () => undefined,
    pathDelimiter: ":",
  });
  try {
    withPlatform("linux", () => {
      withEnvOverrides({ PATH: "/bin:/usr/bin" }, () => assert.equal(autoDetectChrome(), p));
    });
  } finally { restore(); }
});

test("autoDetect: linux — snap chromium", () => {
  const p = "/snap/bin/chromium";
  const restore = patchInternals({
    fsExistsSync: (x) => x === p,
    fsAccessSync: () => undefined,
    pathDelimiter: ":",
  });
  try {
    withPlatform("linux", () => {
      withEnvOverrides({ PATH: "/snap/bin" }, () => assert.equal(autoDetectChrome(), p));
    });
  } finally { restore(); }
});

test("autoDetect: linux — nothing returns null", () => {
  const restore = patchInternals({ fsExistsSync: () => false });
  try {
    withPlatform("linux", () => {
      withEnvOverrides({ PATH: "" }, () => assert.equal(autoDetectChrome(), null));
    });
  } finally { restore(); }
});

test("autoDetect: windows — PATH hit chrome.exe", () => {
  const restore = patchInternals({
    // Return true for ANY "chrome" candidate under System32 — PATH lookup tries .COM/.EXE/.BAT/.CMD suffixes.
    fsExistsSync: (p) => p.startsWith("C:\\Windows\\System32\\chrome."),
    fsAccessSync: () => undefined,
    pathDelimiter: ";",
    pathJoin: (...parts) => parts.join("\\"),
  });
  try {
    withPlatform("win32", () => {
      withEnvOverrides({ PATH: "C:\\Windows\\System32" }, () => {
        const r = autoDetectChrome();
        assert.match(r, /^C:\\Windows\\System32\\chrome\./);
      });
    });
  } finally { restore(); }
});

test("autoDetect: windows — Program Files Google Chrome", () => {
  const pf = "C:\\Program Files";
  const p = path.join(pf, "Google", "Chrome", "Application", "chrome.exe");
  const restore = patchInternals({
    fsExistsSync: (x) => x === p,
    fsAccessSync: () => undefined,
    pathDelimiter: ";",
    pathJoin: path.join,
  });
  try {
    withPlatform("win32", () => {
      withEnvOverrides({ PATH: "", ProgramFiles: pf, "ProgramFiles(x86)": pf, LocalAppData: undefined }, () => {
        assert.equal(autoDetectChrome(), p);
      });
    });
  } finally { restore(); }
});

test("autoDetect: windows — LocalAppData fallback", () => {
  const pf = "C:\\Program Files";
  const lad = "C:\\Users\\me\\AppData\\Local";
  const p = path.join(lad, "Google", "Chrome", "Application", "chrome.exe");
  const restore = patchInternals({
    fsExistsSync: (x) => x === p,
    fsAccessSync: () => undefined,
    osHomedir: () => "C:\\Users\\me",
    pathDelimiter: ";",
    pathJoin: path.join,
  });
  try {
    withPlatform("win32", () => {
      withEnvOverrides({ PATH: "", ProgramFiles: pf, "ProgramFiles(x86)": pf, LocalAppData: lad }, () => {
        assert.equal(autoDetectChrome(), p);
      });
    });
  } finally { restore(); }
});

// =========================================================================
// getChromeVersion
// =========================================================================

test("getChromeVersion: success", () => {
  const restore = patchInternals({ cpSpawnSync: () => ({ status: 0, stdout: "Google Chrome 131.0.6778.85\n", stderr: "", error: null }) });
  try {
    const r = getChromeVersion("/c");
    assert.equal(r.ok, true);
    assert.equal(r.version, "Google Chrome 131.0.6778.85");
  } finally { restore(); }
});

test("getChromeVersion: exit != 0 surfaces as error", () => {
  const restore = patchInternals({ cpSpawnSync: () => ({ status: 1, stdout: "", stderr: "lockfile", error: null }) });
  try {
    const r = getChromeVersion("/c");
    assert.equal(r.ok, false);
    assert.match(r.error, /exit code/);
  } finally { restore(); }
});

test("getChromeVersion: ENOENT surfaces as 'file not found'", () => {
  const err = new Error("spawn ENOENT"); err.code = "ENOENT";
  const restore = patchInternals({ cpSpawnSync: () => ({ error: err, status: null, stdout: "", stderr: "" }) });
  try {
    const r = getChromeVersion("/c");
    assert.equal(r.ok, false);
    assert.match(r.error, /not found/);
  } finally { restore(); }
});

// =========================================================================
// validateChromeExecutable
// =========================================================================

test("validateChromeExecutable: full happy path", () => {
  const restore = patchInternals({
    fsExistsSync: () => true,
    fsAccessSync: () => undefined,
    cpSpawnSync: () => ({ status: 0, stdout: "Chrome OK\n", stderr: "", error: null }),
  });
  try {
    const r = validateChromeExecutable("/good");
    assert.equal(r.ok, true);
    assert.equal(r.version, "Chrome OK");
  } finally { restore(); }
});

test("validateChromeExecutable: path empty", () => {
  assert.equal(validateChromeExecutable("").ok, false);
});

test("validateChromeExecutable: path does not exist", () => {
  const restore = patchInternals({ fsExistsSync: () => false });
  try {
    const r = validateChromeExecutable("/nope");
    assert.equal(r.ok, false);
    assert.match(r.error, /does not exist/);
  } finally { restore(); }
});

test("validateChromeExecutable: exists but EACCES", () => {
  const accessErr = new Error("EACCES"); accessErr.code = "EACCES";
  const restore = patchInternals({
    fsExistsSync: () => true,
    fsAccessSync: () => { throw accessErr; },
  });
  try {
    const r = validateChromeExecutable("/bad");
    assert.equal(r.ok, false);
    assert.match(r.error, /not executable/i);
  } finally { restore(); }
});

// =========================================================================
// formatChromeNotFound
// =========================================================================

test("formatChromeNotFound: mentions platform, candidates, fixes on darwin", () => {
  withPlatform("darwin", () => {
    const msg = formatChromeNotFound({});
    assert.match(msg, /ERROR.*Chrome/);
    assert.match(msg, /Platform.*darwin/);
    assert.match(msg, /Applications.*Google Chrome/);
    assert.match(msg, /--chrome.*path/);
    assert.match(msg, /CHROME_BIN/);
  });
});

test("formatChromeNotFound: mentions fix options on linux", () => {
  withPlatform("linux", () => {
    const msg = formatChromeNotFound({});
    assert.match(msg, /Platform.*linux/);
    assert.match(msg, /google-chrome/);
  });
});
