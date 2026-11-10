// 生成 P1 回归基线用的 .widscan58 回放样本。GPL-3.0-or-later。
//
// 数据来源只有一处：tests/protocol/golden/frames-v1.json 的 streams[].feed
// （那 8 条流是解析器对拍夹具，字节由 web/src/serial/parser.ts 与
// firmware/src/proto/frames.h 的断言尺寸构造）。这里不造电平、不补帧、不加
// PROFILE/STATUS 前言——那些行在真机录制里来自设备回复，合成出来就是假设备身份。
//
// 所以这份样本能证明的是「回放管道 + 解析器 + 视图」这条链在历史字节流上跑得通，
// 不能证明任何真实射频数值。文件旁边的 .meta.json 把这一点写死，页面状态条也读它。
//
//   node tests/fixtures/make-baseline-recording.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const GOLDEN = resolve(here, "../protocol/golden/frames-v1.json");
const OUT = join(here, "baseline-golden-2026-11-04.widscan58");

const CHUNK_MS = 50; // 每块之间的固定节拍；不是设备的真实时序，只是回放速度
const doc = JSON.parse(readFileSync(GOLDEN, "utf8"));

const records = [];
let t = 0;
for (const stream of doc.streams) {
  for (const hex of stream.feed) {
    const bytes = Uint8Array.from(hex.match(/../g) ?? [], (h) => parseInt(h, 16));
    records.push({ t, bytes });
    t += CHUNK_MS;
  }
  t += CHUNK_MS; // 一条流结束后的空档
}

const buf = new ArrayBuffer(records.reduce((n, r) => n + 8 + r.bytes.length, 0));
const dv = new DataView(buf);
let pos = 0;
for (const r of records) {
  dv.setUint32(pos, r.t, true);
  dv.setUint32(pos + 4, r.bytes.length, true);
  new Uint8Array(buf, pos + 8, r.bytes.length).set(r.bytes);
  pos += 8 + r.bytes.length;
}
writeFileSync(OUT, Buffer.from(buf));

const sha256 = createHash("sha256").update(Buffer.from(buf)).digest("hex");
writeFileSync(
  `${OUT}.meta.json`,
  JSON.stringify(
    {
      format: "widscan58",
      record: "u32 ms | u32 length | bytes (LE)",
      source: "synthetic",
      origin: "tests/protocol/golden/frames-v1.json",
      origin_sha256: createHash("sha256").update(readFileSync(GOLDEN)).digest("hex"),
      streams: doc.streams.map((s) => s.name),
      chunks: records.length,
      duration_ms: t,
      bytes: buf.byteLength,
      sha256,
      caveat:
        "合成样本：只证明回放/解析/视图链路可跑，不证明固件真实字节，也不含任何真实射频电平。",
    },
    null,
    2,
  ) + "\n",
);
console.log(`widscan58 -> ${OUT}`);
console.log(`chunks=${records.length} bytes=${buf.byteLength} duration_ms=${t}`);
console.log(`sha256=${sha256}`);
