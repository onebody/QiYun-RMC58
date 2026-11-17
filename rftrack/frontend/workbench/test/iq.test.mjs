/**
 * 原始 I/Q 的纯计算。**判据来自信号本身，不来自被测代码**：
 *
 * - FFT 对着一个独立的朴素 DFT 逐格比（O(n²) 那份只有 40 行，不会与被测的 radix-2 一起错）；
 * - 标量事实对着一组手算得出来的样本比；
 * - 谱的归一化对着"满量程 10-bit 复正弦在自己的 bin 里读 0 dBFS"这条口径比——这是与单机侧
 *   共用的约定，也是运营者判断"底噪是不是真的低了 6 dB"的唯一依据。
 *
 * 不足一段 FFT 时**不许画假底噪**：`segs=0` 那一条钉的就是这件事。
 */

import assert from "node:assert/strict";
import test from "node:test";

import { fft, spectrum, summarize } from "../iq.js";
import { toneSnapshot } from "./support/control-fixture.mjs";

/** 独立参照：朴素 DFT。故意写得笨，笨到不会与 radix-2 犯同一个错。 */
function dft(re, im) {
  const n = re.length;
  const outRe = new Array(n).fill(0);
  const outIm = new Array(n).fill(0);
  for (let k = 0; k < n; k += 1) {
    for (let j = 0; j < n; j += 1) {
      const ang = (-2 * Math.PI * k * j) / n;
      outRe[k] += re[j] * Math.cos(ang) - im[j] * Math.sin(ang);
      outIm[k] += re[j] * Math.sin(ang) + im[j] * Math.cos(ang);
    }
  }
  return { re: outRe, im: outIm };
}

test("fft 与朴素 DFT 逐格一致（n=8 与 n=64，输入是确定的伪随机整数）", () => {
  for (const n of [8, 64]) {
    const re = Array.from({ length: n }, (_, j) => ((j * 37) % 23) - 11);
    const im = Array.from({ length: n }, (_, j) => ((j * 53) % 19) - 9);
    const want = dft(re, im);
    const gotRe = re.slice();
    const gotIm = im.slice();
    fft(gotRe, gotIm);
    for (let k = 0; k < n; k += 1) {
      assert.ok(Math.abs(gotRe[k] - want.re[k]) < 1e-9, `n=${n} bin=${k} 实部 ${gotRe[k]} ≠ ${want.re[k]}`);
      assert.ok(Math.abs(gotIm[k] - want.im[k]) < 1e-9, `n=${n} bin=${k} 虚部 ${gotIm[k]} ≠ ${want.im[k]}`);
    }
  }
});

test("summarize：直流、RMS、峰值、lsb 与量程都对着一组手算样本", () => {
  // i=[3,-1,3,-1] q=[4,0,4,0]：直流 (1,2)，去直流后每点功率 8，RMS=√8。
  const facts = summarize({ i: [3, -1, 3, -1], q: [4, 0, 4, 0], n: 4, shift: 2, loKhz: 5658000, rateHz: 12_000_000 });
  assert.equal(facts.dcI, 1);
  assert.equal(facts.dcQ, 2);
  assert.equal(facts.peak, 4);
  assert.ok(Math.abs(facts.rms - Math.sqrt(8)) < 1e-12);
  assert.equal(facts.lsb, 4, "shift=2 → 传输计数要乘 4 才是 10-bit ADC 计数");
  assert.equal(facts.range, 8, "峰值太小就落到最小量程 8，否则整张星座图缩成一个点");
  assert.equal(facts.conjugated, false);
});

test("summarize：量程跟着峰值走，共轭与 shift 原样带出来", () => {
  const facts = summarize({ i: [100, -100], q: [0, 0], n: 2, shift: 3, conjugated: true, loKhz: 5740000, rateHz: 8_000_000 });
  assert.equal(facts.range, 115, "ceil(100 × 1.15)");
  assert.equal(facts.lsb, 8);
  assert.equal(facts.conjugated, true);
  assert.equal(facts.loKhz, 5740000);
  assert.equal(facts.rateHz, 8_000_000);
});

test("spectrum：满量程复正弦在自己的 bin 里读 0 dBFS，别的 bin 远低于它", () => {
  const shift = 2;
  const snapshot = toneSnapshot({ n: 4096, shift, bin: 100, amp: 512 / 2 ** shift });
  const band = spectrum(snapshot, { nfft: 1024 });
  assert.equal(band.segs, 4);
  // dbs 做过 fftshift：bin 100 落在下标 100 + 512。
  const at = 100 + 512;
  assert.ok(Math.abs(band.dbs[at]) < 0.05, `满量程该读 0 dBFS，实得 ${band.dbs[at]}`);
  let peakAt = 0;
  band.dbs.forEach((value, k) => { if (value > band.dbs[peakAt]) peakAt = k; });
  assert.equal(peakAt, at, "峰必须落在信号那一格，不是别处");
  assert.ok(band.dbs[100] < -40, `远离信号的那一格该是底噪，实得 ${band.dbs[100]}`);
});

test("spectrum：幅值减半 → 那一格低 6 dB（纵轴真是 dB，不是线性功率）", () => {
  const full = spectrum(toneSnapshot({ n: 2048, shift: 2, bin: 100, amp: 128 }), { nfft: 1024 });
  const half = spectrum(toneSnapshot({ n: 2048, shift: 2, bin: 100, amp: 64 }), { nfft: 1024 });
  const at = 100 + 512;
  const drop = full.dbs[at] - half.dbs[at];
  assert.ok(Math.abs(drop - 6.0206) < 0.05, `半幅该低 6.02 dB，实得 ${drop}`);
});

test("spectrum：横轴是这次抓取的真实频率跨度（LO ± 采样率/2）", () => {
  const band = spectrum(toneSnapshot({ n: 1024, loKhz: 5658000, rateHz: 12_000_000 }), { nfft: 1024 });
  assert.equal(band.segs, 1);
  assert.equal(band.f0, 5652000);
  assert.equal(band.f1, 5664000);
  assert.equal(band.nfft, 1024);
  assert.ok(Number.isInteger(band.lo / 10), `纵轴下界该落在 10 的整数倍上，实得 ${band.lo}`);
  assert.equal(band.hi, Math.ceil(Math.max(...band.dbs) / 10) * 10 + 5);
});

test("spectrum：不足一段 1024 点就不画谱——dbs 是空的，不是一条假底噪", () => {
  const band = spectrum(toneSnapshot({ n: 512 }), { nfft: 1024 });
  assert.equal(band.segs, 0);
  assert.deepEqual(band.dbs, []);
  assert.equal(band.f0, band.loKhz);
  assert.equal(band.f1, band.loKhz);
});

test("spectrum：直流被减掉，所以 bin 0 不该因为偏置而顶起来", () => {
  const snapshot = toneSnapshot({ n: 2048, shift: 2, bin: 100, amp: 128 });
  const biased = { ...snapshot, i: snapshot.i.map((v) => v + 40), q: snapshot.q.map((v) => v - 25) };
  const clean = spectrum(snapshot, { nfft: 1024 });
  const dirty = spectrum(biased, { nfft: 1024 });
  const at = 100 + 512;
  assert.ok(Math.abs(clean.dbs[at] - dirty.dbs[at]) < 0.05, "加一个直流偏置不该改变信号那一格");
  assert.ok(dirty.dbs[512] < -40, `直流那一格该被减掉，实得 ${dirty.dbs[512]}`);
});