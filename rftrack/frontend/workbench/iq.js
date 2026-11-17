/**
 * 原始 I/Q 的纯计算：均值/峰值/RMS、客户端 FFT 与 dBFS 谱。**不含 Vue、不含 Canvas**，
 * 所以 `node --test` 能拿一组已知样本钉住数字，不必开浏览器看图。
 *
 * 算法逐行沿用原单机控制台 `web/src/views/rawiq.ts`：同一份快照在两套页面里必须算出
 * 同一个 RMS 与同一条谱，否则"站点侧看到的底噪比单机侧高 6 dB"会被当成板子的问题。
 * 两处有意不同：
 * - 输入是后端 `RawIqAssembler` 拼好的**普通数组**（JSON 里没有 Int8Array），不是 TypedArray。
 * - 谱的纵轴范围与星座图的量程在这里算一次（`summarize().range`），不在两个绘制函数里各算一遍。
 */

/** 原地 radix-2 FFT。长度必须是 2 的幂（调用方只传 1024）。 */
export function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    for (let i = 0; i < n; i += len) {
      for (let k = 0; k < len / 2; k++) {
        const wr = Math.cos(ang * k);
        const wi = Math.sin(ang * k);
        const a = i + k;
        const b = a + len / 2;
        const tr = re[b] * wr - im[b] * wi;
        const ti = re[b] * wi + im[b] * wr;
        re[b] = re[a] - tr;
        im[b] = im[a] - ti;
        re[a] += tr;
        im[a] += ti;
      }
    }
  }
}

/**
 * 一份快照的标量事实。`lsb` 是"传输计数 → 10-bit ADC 计数"的倍率（固件右移 `shift` 位后
 * 才发出来），页面上凡是印 ADC 计数的地方都要乘它——不乘就是把量程说小了 2^shift 倍。
 */
export function summarize(snapshot) {
  const { i, q, n } = snapshot;
  let mi = 0;
  let mq = 0;
  let peak = 0;
  for (let j = 0; j < n; j++) {
    mi += i[j];
    mq += q[j];
    peak = Math.max(peak, Math.abs(i[j]), Math.abs(q[j]));
  }
  mi /= n;
  mq /= n;
  let power = 0;
  for (let j = 0; j < n; j++) power += (i[j] - mi) ** 2 + (q[j] - mq) ** 2;
  const lsb = 2 ** snapshot.shift;
  return {
    n,
    loKhz: snapshot.loKhz,
    rateHz: snapshot.rateHz,
    shift: snapshot.shift,
    conjugated: Boolean(snapshot.conjugated),
    lsb,
    dcI: mi,
    dcQ: mq,
    rms: Math.sqrt(power / n),
    peak,
    // 星座图与时间波形共用一个量程：正常增益下噪声只占几个 ADC 计数，
    // 按满量程画会把整张图缩成中心一个点。
    range: Math.max(8, Math.ceil(peak * 1.15)),
  };
}

/**
 * 平均 Hann 窗 1024 点 FFT → dBFS/bin，横轴是**绝对频率 kHz**（LO ± 采样率/2）。
 *
 * 归一化口径与单机侧一致：满量程 10-bit 复正弦（幅值 512/2^shift 个传输计数）在自己的
 * bin 里读 0 dB，Hann 窗的相干增益是 nfft/2。`segs` 为 0（快照不足 1024 点）时不画谱，
 * 由调用方说"样本不够"，而不是画一条全是 -200 dB 的假底噪。
 */
export function spectrum(snapshot, { nfft = 1024 } = {}) {
  const facts = summarize(snapshot);
  const segs = Math.floor(snapshot.n / nfft);
  if (!segs) return { ...facts, segs: 0, nfft, dbs: [], f0: facts.loKhz, f1: facts.loKhz };
  const acc = new Float64Array(nfft);
  const re = new Float64Array(nfft);
  const im = new Float64Array(nfft);
  for (let sg = 0; sg < segs; sg++) {
    for (let j = 0; j < nfft; j++) {
      const w = 0.5 - 0.5 * Math.cos((2 * Math.PI * j) / nfft);
      re[j] = (snapshot.i[sg * nfft + j] - facts.dcI) * w;
      im[j] = (snapshot.q[sg * nfft + j] - facts.dcQ) * w;
    }
    fft(re, im);
    for (let k = 0; k < nfft; k++) acc[(k + nfft / 2) % nfft] += re[k] * re[k] + im[k] * im[k];
  }
  const fs = 512 / 2 ** snapshot.shift;
  const norm = 1 / (segs * ((fs * nfft) / 2) ** 2);
  const dbs = Array.from(acc, (value) => 10 * Math.log10(value * norm + 1e-20));
  return {
    ...facts,
    segs,
    nfft,
    dbs,
    f0: snapshot.loKhz - snapshot.rateHz / 2000,
    f1: snapshot.loKhz + snapshot.rateHz / 2000,
    lo: Math.floor(Math.min(...dbs) / 10) * 10,
    hi: Math.ceil(Math.max(...dbs) / 10) * 10 + 5,
  };
}