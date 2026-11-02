// L1: pretend to be the relay. The bytes are a real C5 capture (never
// synthetic - inventing scan frames is the red line), chunked the way
// relay_pipe.c chunks them, paced at the C5's measured 37.0 KB/s, and sent to
// the bridge's node endpoint exactly as the device would. Proves the byte path
// and the platform's parser; proves nothing about the radio or the wire.
//
// It waits for a page (the backend's dialer) to attach to the endpoint before
// the first byte leaves: the bridge forwards a node's bytes only to the page
// that is attached, so bytes sent before the dial would be dropped mid-frame
// and the crc/resync counters we are about to read would describe that hole
// instead of the pipe.
//
// usage: node rftrack/tools/relay_chunk_replay.mjs --file /tmp/w58-probe/dual-C5.bin \
//          --url ws://127.0.0.1:8011/node?id=fake-head --seconds 60 [--chunk 1024]
const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : dflt;
};
const file = opt("file", "");
const url = opt("url", "");
const seconds = Number(opt("seconds", "60"));
const chunk = Number(opt("chunk", "1024"));
const RATE = 37.0 * 1024; // the C5's measured USB-path throughput
if (!file || !url) {
  console.error("need --file and --url");
  process.exit(2);
}
if (!Number.isInteger(chunk) || chunk < 1) {
  console.error("--chunk must be a positive integer");
  process.exit(2);
}
const { readFileSync } = await import("node:fs");
const all = readFileSync(file);

// Node 22 ships a WebSocket client; `ws` only sits in web/node_modules and a
// bare import from rftrack/tools/ cannot resolve it.
const target = new URL(url);
const nodeId = target.searchParams.get("id");
const endpointsUrl = `${target.protocol === "wss:" ? "https" : "http"}://${target.host}/api/endpoints`;

const sock = new WebSocket(url);
await new Promise((res, rej) => {
  sock.addEventListener("open", res);
  sock.addEventListener("error", rej);
});

const pageAttached = async () => {
  const res = await fetch(endpointsUrl);
  const eps = (await res.json()).endpoints ?? [];
  return !!eps.find((e) => e.id === `node:${nodeId}`)?.busy;
};
const deadline = Date.now() + 90_000;
for (;;) {
  if (await pageAttached()) break;
  if (Date.now() >= deadline) {
    console.error(`no page attached to node:${nodeId} within 90s - is the backend dialer enabled?`);
    process.exit(3);
  }
  await new Promise((r) => setTimeout(r, 500));
}

let sent = 0;
let sends = 0;
let off = 0;
const start = Date.now();
await new Promise((res) => {
  const timer = setInterval(() => {
    // Budget from elapsed time, not a fixed per-tick count, so a delayed tick
    // cannot quietly turn "measured rate" into a number nobody checked.
    const elapsed = (Date.now() - start) / 1000;
    const budget = Math.min(Math.floor(elapsed * RATE), all.length - (all.length % chunk));
    while (off + chunk <= budget) {
      sock.send(all.subarray(off, off + chunk));
      off += chunk;
      sent += chunk;
      sends += 1;
    }
    if (elapsed >= seconds || off + chunk > all.length) {
      clearInterval(timer);
      res();
    }
  }, 50);
});
// The relay's UART reads return whatever arrived, so a short tail is normal -
// send it rather than silently dropping the last few bytes of the capture.
if (off < all.length) {
  sock.send(all.subarray(off));
  sent += all.length - off;
  sends += 1;
}
await new Promise((r) => setTimeout(r, 300)); // let the close flush what is queued
sock.close();
const secs = (Date.now() - start) / 1000;
console.log(
  JSON.stringify({
    node_id: nodeId,
    chunk,
    source_bytes: all.length,
    sent,
    sends,
    seconds: Number(secs.toFixed(1)),
    kb_per_s: Number((sent / 1024 / secs).toFixed(1)),
    target_kb_per_s: Number((RATE / 1024).toFixed(1)),
  }),
);
