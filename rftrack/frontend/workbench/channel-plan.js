/**
 * 5.8 GHz FPV 频道表与 `CHANNELS` 命令。**纯数据，不含 Vue、不含 DOM**。
 *
 * 这张表是原单机控制台 `web/src/model/channelPlan.ts` 的同一份：同一套频道在两套页面里
 * 必须是同一个频率，否则运营者在站点侧改了计划、回到单机侧看到的却是另一张表。
 * `test/channel-plan.test.mjs` 逐值对拍两份表，改一边不改另一边会红。
 *
 * 与单机侧的一处**有意不同**：这里没有 localStorage。单机控制台把计划存在浏览器里，
 * 因为它是"一人一板"；站点侧一块板可能被别的运营者、别的页面改过，浏览器里那份旧计划
 * 会被当成板子的当前状态发出去。所以这一页的计划一律从板子自己读回（`PROFILE?` 的
 * `channels` 带着名字与 kHz，`commands.c` 的 `send_profile`），页面只当编辑器。
 */

export const BANDS = [
  { letter: "R", name: "Raceband", mhz: [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917] },
  { letter: "A", name: "Boscam A", mhz: [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725] },
  { letter: "B", name: "Boscam B", mhz: [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866] },
  { letter: "E", name: "Boscam E", mhz: [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945] },
  { letter: "F", name: "Fatshark", mhz: [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880] },
  { letter: "L", name: "Low band", mhz: [5362, 5399, 5436, 5473, 5510, 5547, 5584, 5621] },
];

/** 固件一次最多扫这么多档（`PROFILE_MAX_CHANNELS`）。 */
export const MAX_CHANNELS = 8;

/** 频道名（"R1"）对应的频率 MHz；认不出回 undefined，不猜一个"大概是 R 段"。 */
export function channelMhz(name) {
  const band = BANDS.find((row) => row.letter === String(name)[0]);
  const n = Number(String(name).slice(1));
  return band && Number.isInteger(n) && n >= 1 && n <= 8 ? band.mhz[n - 1] : undefined;
}

/**
 * 只留认识的名字、去重、最多 8 档，按频率升序（同频保持表内顺序）——
 * 档位号要顺着频段往上走，与全景图一致。
 */
export function normalizePlan(names) {
  const order = BANDS.flatMap((band) => band.mhz.map((_, j) => `${band.letter}${j + 1}`));
  const known = [...new Set(names)].filter((name) => channelMhz(name) !== undefined).slice(0, MAX_CHANNELS);
  return known.sort((a, b) => channelMhz(a) - channelMhz(b) || order.indexOf(a) - order.indexOf(b));
}

/** `CHANNELS R1=5658 R2=5695 …`：每一档按它自己的 5.8 GHz 频率下发。 */
export function channelsCommand(names) {
  return `CHANNELS ${names.map((name) => `${name}=${channelMhz(name)}`).join(" ")}`;
}

/** 还没读到板子的计划时编辑器里的初始选择，与单机侧的默认一致（Raceband 全段）。 */
export const DEFAULT_PLAN = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"];

/**
 * 板子自己报的计划（`PROFILE?` → `{channels:[{id,name,khz}]}`）。
 *
 * 认不出的名字**单独列出来而不是丢掉**：缩放档（profile 把自己的频段映射过来）会报出
 * 这张表里没有的名字，静默过滤等于替运营者改了板子的计划，还让他以为格子显示的就是全部。
 */
export function planFromProfile(profile) {
  const names = ((profile && profile.channels) || []).map((row) => row.name);
  return {
    names,
    known: names.filter((name) => channelMhz(name) !== undefined),
    unknown: names.filter((name) => channelMhz(name) === undefined),
  };
}