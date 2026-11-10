# widscan58：RotorHazard UART 旁路监听 + SoftAP 对外发布 vs 设备主动发布 API（ChatGPT 采集件·第二轮）

> 接第一轮「ESP32-C5 ↔ ESP32-S3 双芯片通讯方案」的追问，正文为 ChatGPT 回复原文，未经改写。

---

## 一、采集元信息

| 项目 | 内容 |
| --- | --- |
| 平台 | ChatGPT 网页版 https://chatgpt.com/ |
| 账户状态 | 已登录（免费版），复用用户本人浏览器会话，未读取任何凭据 |
| 会话标题 | 双芯片通信方案 |
| 会话链接 | https://chatgpt.com/c/6ae06a2c-b3b0-83ea-bb49-22537505ed8c |
| 轮次 | 第 2 轮（同一会话内追问，ChatGPT 带着第一轮的全部约束） |
| 采集时间 | 2026-10-27 17:34 – 17:37 CST |
| 采集方式 | 浏览器自动化（独立 Agent 窗口，`bsk` CLI）；生成耗时 99 s |
| 回复轮次 | 本轮单轮完成（回复中不含单选组与交互按钮） |
| 正文规模 | 29.4 KB / 11 个二级章节 / 14 张表格 / 21 个代码块 / 51 个标题 |
| 上一轮采集件 | repo/docs/C5-S3-双芯片通讯方案-ChatGPT采集.md |

---

## 二、我的追问（第二轮，原文）

前提沿用第一轮的固件现状（RH UART0 独占、C5 剩约 5 KB 堆、S3 兼作 2.4 GHz 采集目标），因此这里没有重复粘贴约束细节，ChatGPT 仍能从同一会话上下文取到。

```
接着上面那套方案（C5 用独立 UART 连 S3、S3 负责 Wi-Fi 上报），我现在有两个新的想法，请分别给出可行性判断 + 完整设计 + 取舍对比，最后明确推荐。

【方式一：S3 以"公用/旁路"方式直接读取 C5 与树莓派 RotorHazard 之间那条现成的 UART，然后 S3 起 AP 供别人取数据】

现状（都是我这边的真实约束，请以此为前提，不要改成假设）：
- C5 的 UART0 = GP11 TX / GP12 RX，115200 8N1，跑 RotorHazard 串口节点协议 API level 36，单端口 8 个 seat，等同 S32_BPill 的 8 接收机；树莓派上的 RotorHazard 作为主控，约 100 ms 周期用读命令轮询。
- 协议特征：读命令 ≤ 0x50、写命令 > 0x50；大端；sum8 校验；写超时 100 ms；RSSI/SNR 编码为 1–254，0.25 dB 步进；默认 EnterAt/ExitAt = 125/105。
- 板端已完成计圈，过门时间取"最高平台"的中点，lap pass sequence 与遥测里都有。
- C5 侧硬约束：esp_wifi_start 之后只剩约 5 KB 堆、静态 RAM 只剩几百字节；UART sink 没有环形队列（回复直接在 uart_write_bytes 上阻塞写出，加 8 KB ring 曾导致回复缓冲分配失败、板子活着但不回话）；扫描热路径在 IRAM；Wi-Fi 必须保持 WIFI_MODE_NULL。
- 已有射频归属仲裁：UART 最近 2 s 有流量时，USB 的 PROTOCOL MONITOR 返回 ERR uart_rh。

我要的形态：不改动这条 UART 的既有职责，S3 以旁路方式把链路上的数据读出来（比如只并接 C5 TX 那根线做被动监听，必要时也监听树莓派 TX 方向），解析 RH 的"命令—回复"问答帧，拿到计圈事件与 RSSI/噪声/门限遥测；然后 S3 开 SoftAP，让现场别人的设备连进来取这些数据。

请回答：
1. 电气与协议上"三线并接（Pi — C5 — S3 只听不发）"是否成立？115200 8N1 被动监听的实际副作用（总线负载、上下拉、共地、线长上限、两端同时驱动的风险）；如果 S3 误把 TX 也接上去会发生什么；要不要缓冲器/光耦/单向隔离，或者用二极管+上拉的简易方案。
2. sniffer 如何可靠对齐帧？RH 是主从问答式，只监听单向能拿到哪些数据、拿不到哪些；要同时看两个方向才能把命令与回复配对，请给出解析状态机设计（同步、sum8 校验失败、丢字节、半帧、主控重启、节点掉线重连时的重同步与"我是不是漏了一帧"的可检测性）。
3. 与现有 RH 的冲突面：port/seat 计数、节点地址语义、hub 的超时与掉线判定、主控轮询节奏；旁路监听本身会不会影响 RH 计时精度或 C5 的扫描实时性（量化说明为什么"只听不改"是零影响的，或者指出真正有风险的地方）。
4. S3 侧完整架构与资源：监听任务、解码任务、SoftAP、HTTP/WS 服务的优先级/栈/缓冲分配；S3 做 SoftAP 最多能带多少 STA、信道与带宽怎么定；关键冲突点——S3 在这套固件里同时还是 2.4 GHz 采集目标（k = 83.5/296 线性缩放），AP 与它自己的 PHY 采集能否共存、不能共存怎么取舍；能否用 esp_http_server + LittleFS 把现有 Web UI 直接从 S3 flash 提供给访客；权限边界（访客只读、不能下发写命令 > 0x50）。

【方式二：不做并线监听，改为在设备侧直接对外发布：配一个 Wi-Fi 地址 + 一个 API 接口向外推，WS / HTTP / MQTT 三种都要】

请回答：
5. 三种方式的适配性对比表：连接方向、建立时延、单帧开销、RAM/Flash 占用、断线语义、是否需要 broker、鉴权能力、局域网/NAT、浏览器与手机端接入难度、多订阅者扇出。并说明在你的实际消费者（RotorHazard、手机 App、现场观众浏览器、第三方大屏/Home Assistant）下各自该选哪种。
6. 数据模型与接口设计：scan 结果、lap 事件、levels/metrics、节点状态四类数据的字段/单位/频率/背压策略；事件必须带 ID 去重与"至少一次"投递的实现；MQTT 的 topic 树、QoS、retain、LWT 与遗嘱在线状态；WebSocket 的帧类型与心跳、重连补发；HTTP 用长轮询还是 SSE、以及如何避免 100 ms 轮询打爆 S3。
7. 如果有人坚持让 C5 自己出网：请给出 ESP32-C5 打开 WIFI_MODE_STA 或 AP + lwIP + mbedTLS 的内存账（静态 RAM、tcpip 任务栈、lwIP pbuf/mbox、TLS 握手峰值堆），并据此说明为什么在"必须保持 WIFI_MODE_NULL、只剩 5 KB 堆"的前提下不可行；给出可接受的退路（C5 只出 UART 数据、S3 出网；或 C5 完全不参与网络）。
8. 安全与运维：WPA2/WPA3 与 AP 混合模式、局域网与公网边界、API Key/token 放哪、TLS 需要时间同步（无 RTC 怎么办）、证书校验与 mosquitto/自建 broker 的凭据、日志与固件更新、同一赛事多场地多设备的地址与 topic 规划。

最后请一次性给全：
- 方式一与方式二的对比表（可行性、对现有 RH 的侵入性、实时性、丢数据风险、开发工作量、可维护性、现场部署复杂度）。
- 明确推荐哪一种（或哪种组合），以及"为什么不选另一种"的具体理由，不要两边都不得罪。
- 分阶段实施计划与每阶段验收判据，必须包含 RH 侧回归验证：证明旁路监听不影响 RotorHazard 的计圈与掉线判定。
- 关键代码骨架（ESP-IDF 5.5 风格）：S3 侧被动监听 UART 驱动 + RH 问答帧解析状态机 + SoftAP 启动 + HTTP/WS 只读服务 + MQTT/WS 发布器；C5 侧只允许新增一个不额外占堆的输出点（如果确实必需），并给出它的内存代价估算。
- 表格和代码块都要完整。
```

---

## 三、第二轮回复（原文整理）

## widscan58 双方案评审：RotorHazard UART 旁路监听 vs 设备主动发布 API

先给结论：**我推荐以方式二为主，方式一作为可选的只读观测功能；但在你当前的内存和射频约束下，S3 必须先解决“SoftAP 与 2.4 GHz 采集能否共存”的问题。**

具体而言：

- **C5：** 继续只运行现有 5.8 GHz 接收、计圈和 RotorHazard API level 36 串口节点协议。保留 `WIFI_MODE_NULL`，不新增 Wi-Fi、TCP/IP、TLS 或 MQTT。

- **S3：** 负责网络、Web UI、手机端 API 和数据汇聚。

- **方式一：** S3 可以监听现有 RH UART 的两条 TX 信号，但必须是电气上真正的只接收旁路。它不能向 Pi—C5 总线发送任何字节，也不能向现有协议注入查询命令。

- **方式二：** 优先由 S3 将数据通过 WebSocket、HTTP 和 MQTT 发布给不同消费者；C5 的现有 UART 不承担新网络协议。

- **数据可靠性：** 扫描曲线可以覆盖旧数据；计圈事件必须有唯一 ID、持久或有界重试语义、去重和明确的失败状态。

这里有两个不能回避的技术问题。

第一，**旁路监听能观察到什么，取决于数据是否真实出现在 UART 线上**。如果 C5 的计圈事件只存在于内部状态，未被现有 RH 回复帧编码，就不能靠 S3 监听 UART 得到它。你提到 lap pass sequence 已存在于遥测里，这是一个很好的基础，但仍需核对实际回复命令和字段定义。

第二，S3 的 SoftAP 会使用 2.4 GHz Wi-Fi 射频。如果你的 2.4 GHz 线性缩放采集算法依赖同一颗 S3 的 Wi-Fi PHY、信道扫描、RSSI 或接收时序，**不能默认 SoftAP 与该采集任务同时工作时结果仍然有效**。这个冲突需要单独验证，不能仅通过提高任务优先级解决。

### 1. 方式一：旁路监听现有 RotorHazard UART

#### 1.1 可行性判断

- **电气上可行，协议上需要双向监听**
  S3 可以作为第三个 UART 接收器，分别监听 Pi TX→C5 RX 和 C5 TX→Pi RX 两条单向信号，不参与总线驱动。

- **“只接 C5 TX”不能完整还原问答过程**
  它能解析 C5 发出的回复，但如果回复不包含足够的命令标识，就不能可靠判断该回复对应哪条读命令，也不能确认 Pi 是否发送过被漏掉的命令。

- **C5 不增加任何输出点也能实现**
  S3 自己解码现有 RH 协议即可；C5 无须修改计圈逻辑，也无须增加 UART 写操作。这是方式一最大的优势。

#### 1.2 正确的三方电气拓扑

这里的“三线并接”不是把三台设备的 TX 全部接在一起，而是每个发送端分别连接两个接收端。

- **树莓派 RotorHazard**
  TX_Pi：主控查询、写命令
  同一条信号线，接两个 RX 输入

- **C5 RX**
  原有 RH 接收端
  S3 RX_A
  被动监听主控方向

- **ESP32-C5**
  TX_C5：RH 回复、遥测数据
  同一条信号线，接两个 RX 输入

- **Pi RX**
  原有 RH 接收端
  S3 RX_B
  被动监听节点方向

三台设备共地。S3 的两个 TX 引脚都不接入现有总线；如果 S3 通过 USB 连接电脑，也要避免其他软件意外将监听 UART 配置为输出。

实际接线至少需要四条信号/参考连接：两条 UART 信号线各自分接到 S3 的 RX，加上公共 GND。若 Pi、C5、S3 已经通过其他接口可靠共地，GND 不必重复接多次。

#### 1.3 115200 8N1 的电气风险

115200 baud 的单比特时间约为：

$$T_{\text{bit}}=\frac{1}{115200}\approx 8.68\ \mu s$$

一个完整 8N1 字节需要 10 bit，即约 86.8 μs。

S3 的 UART RX 是高阻输入，因此并接一个 RX 通常不会明显影响原信号。但“只听不改”不等于数学意义上的零影响：增加的输入电容、支线长度、地电位差和电源噪声都会影响波形。

| 风险 | 评估 | 建议 |
| --- | --- | --- |
| RX 输入负载 | 一般较小，但依赖输入电容和线缆 | 直接短接前先测波形 |
| 上拉/下拉 | 普通推挽 UART 不需要额外上拉 | 不要擅自增加强上拉或下拉 |
| 共地 | 单端 UART 必须有可靠参考地 | 尽量短、避免地线环路 |
| 线长 | 没有适用于所有现场的固定上限 | 板内或短线优先；长距离用差分传输 |
| 信号完整性 | 长支线可能产生反射和边沿畸变 | S3 分接点靠近原信号源/接收点，避免长支线 |
| 双 TX 驱动 | 非常危险 | S3 TX 绝不连接现有 UART 信号线 |
| 电压不匹配 | 可能造成过压或钳位电流 | 确认 Pi 的 UART 电平，不要假定所有 Pi 接口都是 3.3 V |
| 设备掉电 | 可能经保护二极管反向供电 | 需要考虑掉电状态和输入容限 |

**如果 S3 TX 误接到 C5 TX 或 Pi TX：** 两个推挽输出可能同时驱动相反电平，造成过流、波形冲突、串口错误，严重时可能损坏 GPIO。不能依赖“平时不发数据”来保证安全，应该从物理接线上排除这种可能。

##### 要不要缓冲器、光耦或二极管？

- **短距离、同板或相邻开发板：** 先直接并接 RX 输入，最少器件，风险可控。

- **现场长线或地电位差较大：** 使用高输入阻抗、合适电压范围的单向缓冲器；若存在不同电源域或接地风险，采用真正的隔离接口。

- **光耦：** 必须选择在 115200 baud 下具有足够速度和合适传播延迟的器件，普通慢速光耦不一定适用。

- **二极管 + 上拉：** 不推荐。它会改变电平、上升沿和负载特性，不是标准推挽 UART 的通用无损监听方案。

- **RS-422：** 如果未来现场距离长、设备多，最好从一开始就使用差分物理层，而不是把 TTL UART 拉得很长。

#### 1.4 协议解析：单向和双向监听的边界

你给出的 RH 特征足以确定部分校验规则，但还不足以唯一确定每种命令的帧长、命令字段位置、回复头结构和地址字段位置。因此，下面不臆造 API level 36 的二进制帧布局，而是将真实命令表放在解析器配置中。

| 观察来源 | 能确定什么 | 不能可靠确定什么 |
| --- | --- | --- |
| 只听 Pi TX | 主控发出的命令、命令顺序、轮询间隔、写命令 | C5 实际是否收到并成功处理 |
| 只听 C5 TX | 节点回复、回复中的 RSSI/SNR/门限/计圈数据（如果该回复确实携带） | 回复对应哪个请求，除非协议明确提供关联字段 |
| 同时听两方向 | 命令与回复的时间关联、请求超时、部分漏帧迹象 | 不能仅凭串口流证明 C5 内部实际执行了某操作 |
| 再结合 C5 本地事件日志 | 可进一步验证事件与遥测对应关系 | 如果日志路径会干扰实时性，仍需独立评估 |

特别是你提到的“读命令 ≤ `0x50`，写命令 > `0x50`”，它是很有用的分类规则，但**不能把它直接当成帧长度规则**。帧长度必须来自协议表，不能仅凭命令值猜测。

#### 1.5 解析状态机

推荐每个方向分别运行一个轻量解析器，然后由一个配对器做事务关联。

```text
UART RX 字节
寻找帧同步
读取固定头字段
根据命令表确定长度
收集完整帧
长度与sum8正确?
记录错误并滑动重同步
输出完整帧事件
事务配对器
命令-回复关联
遥测与计圈事件缓存
否
是
```

解析器应有以下状态：

1. `SEEK_SYNC`：寻找同步字节或候选帧起点。

2. `READ_HEADER`：读取命令、地址、长度等已确认字段。

3. `READ_BODY`：读取固定或动态长度的数据区。

4. `CHECKSUM`：验证 sum8。

5. `EMIT_FRAME`：交给事务配对器。

6. `RESYNC`：校验失败时，从候选帧内部重新搜索可能的下一帧起点。

由于你没有在消息中提供完整 API level 36 的每种命令帧格式，**我不能负责任地写出一个声称能直接解析所有 RH 帧的通用字节状态机**。正确做法是将 widscan58 中现有的 RH 编解码实现或 RotorHazard 的 API level 36 命令定义作为唯一的帧格式来源，再为每个命令注册长度和字段解析函数。

##### 漏帧检测和重同步

| 情况 | 检测方法 | 恢复方式 |
| --- | --- | --- |
| sum8 错误 | 完整候选帧校验失败 | 滑动窗口重新寻找帧头 |
| 半帧 | 超过帧间超时仍未收齐 | 丢弃当前候选帧，记录截断 |
| Pi 重启 | 轮询节奏中断、命令序号/状态重置（若有） | 清除未完成事务，重新配对 |
| C5 掉线 | 请求后无有效回复，超过 RH 超时 | 记录节点超时，等待下一次有效回复 |
| 丢失一条命令 | Pi 方向帧序列出现断裂迹象 | 若协议没有序号，只能根据事务和时序推断 |
| 丢失一条回复 | 请求未匹配到回复 | 标记事务超时，不能把下一条回复无条件匹配给它 |
| 连续帧粘连 | 多帧连续输入 | 按协议长度逐帧切分 |
| 相同遥测重复 | 相同序号或相同采样时间戳 | 根据事件/采样语义去重 |

若 RH 协议没有序号或事务 ID，旁路程序不能百分之百证明自己漏掉了哪一帧；它只能报告校验失败、帧间断裂、请求超时和关联不确定性。这个区别应体现在 UI 中，不能把推测结果标成确定的“丢帧”。

### 2. 旁路监听对 RotorHazard 的影响

#### 2.1 会不会影响计圈精度？

在理想电气条件下，S3 只增加 RX 输入，不改变 C5 TX 输出电平，不发送任何数据，也不修改 Pi 的轮询节奏，因此不会直接改变 RH 的协议时序。

但不能声称绝对零影响，真正的风险在于：

- 额外输入电容改变信号上升沿；

- 线缆、接地和供电变化引入噪声；

- S3 上电或掉电时对信号线形成额外负载；

- 监听器本身解析错误，导致它对事件时间或状态作出错误判断；

- 若误把监听功能接入原有协议发送路径，就会从旁路变成主动节点。

你已经采用“最高平台中点”作为过门时间，并把 lap pass sequence 放入遥测。旁路解析器应该**原样提取已有事件时间与序号，不重新检测过门、不重新计算圈数，也不覆盖 RH 的权威判定**。

#### 2.2 RH 侧必须回归验证的指标

| 测试 | 对照组 | 监听组 | 验收判据 |
| --- | --- | --- | --- |
| 基础计圈 | S3 未接线 | S3 只监听 | 圈数、lap pass sequence 一致 |
| 过门时间 | 原始 RH 日志 | 原始 RH 日志 | 对应事件时间戳一致；不能只比较 Web UI 刷新时间 |
| 100 ms 轮询 | 未接监听 | 已接监听 | 轮询周期分布无显著变化 |
| 掉线判定 | C5 正常运行 | C5 正常运行 | RH 的超时、掉线和恢复行为一致 |
| C5 断电 | 未接监听 | 已接监听 | RH 仍按原有超时逻辑检测掉线 |
| S3 重启 | 无 S3 | S3 反复上电/重启 | RH 不产生额外写命令或异常回复 |
| 监听负载 | 无访客 | 多访客、高频 WS | C5 计圈和 RH 轮询不受访客数量影响 |
| 电气波形 | 未接监听 | 已接监听 | Pi TX、C5 TX 高低电平、边沿和采样裕量仍合格 |

**不要把“连续跑了半小时没出问题”当成计圈精度的唯一证明。** 应用固定、可重复的门口通过轨迹，对比两组事件 ID、圈数、时间戳和掉线日志，并对关键电气波形做测量。

#### 2.3 RH 的 8-seat 语义和旁路映射

你的一条串口对应 8 个 seat，等同于 S32_BPill 的 8 接收机节点。S3 不应把这 8 个 seat 误识别成 8 个独立的 RotorHazard 节点。

建议内部数据模型分三层：

> 页面代码块标签：纯文本

```text
赛事 Event
  └── RH 节点 Node
       ├── seat 0
       ├── seat 1
       ├── ...
       └── seat 7
```

- `node_id`：RH 协议中真实的节点地址；若协议没有地址字段，就用部署配置中的逻辑节点 ID。

- `seat_id`：该节点内的接收机编号，固定映射到 0–7 或协议实际使用的编号。

- `lap_event_id`：单次计圈事件的唯一 ID，不能直接把 seat ID 或轮询序号当成事件 ID。

旁路解析器只观察现有协议，不应增加新的 RH seat，也不应让 S3 被 RotorHazard 主控识别为额外节点。

### 3. S3 SoftAP 与 2.4 GHz 采集的关键冲突

#### 3.1 SoftAP 资源预算

S3 比 C5 更适合承载 HTTP、WebSocket 和 MQTT。ESP-IDF 5.5 的 S3 SoftAP 配置支持设置频道、加密方式和最大连接数，官方文档中 Wi-Fi 连接数的上限可达 15 个，但这只是驱动允许的连接数，不代表 15 个客户端同时访问网页和 WebSocket 时性能仍然良好。[^1]

建议第一版配置：

| 项目 | 建议值 |
| --- | --- |
| SoftAP 信道 | 1、6、11 中选一个，按现场拥塞情况决定 |
| 带宽 | 20 MHz |
| 加密 | WPA2-PSK/CCMP 起步；支持并验证 WPA3 后再启用混合模式 |
| 最大 STA 数 | 初始限制 4–6 个 |
| DHCP | 由 S3 SoftAP 接口提供 |
| HTTP | 80 端口，仅用于局域网只读 UI |
| WebSocket | /ws，仅发送实时数据与接收只读订阅请求 |
| MQTT | 可选；优先由 S3 STA 连接外部网络中的 broker |
| 访客权限 | 只读，不开放写命令与配置修改 |

如果现场只是手机和一台大屏，4–6 个客户端足够作为初始值。不要为了追求最大连接数而扩大所有缓冲区。

#### 3.2 S3 既做 AP，又做 2.4 GHz 采集，能否共存？

这是本方案的关键风险。

如果你说的 2.4 GHz 线性缩放采集，是利用 S3 自身 Wi-Fi PHY 的 RSSI、扫描或接收活动数据来模拟目标强度，那么 SoftAP 会占用同一套 2.4 GHz 无线资源。即使芯片允许 AP 模式，仍然需要处理信道固定、发射活动、接收窗口和扫描行为之间的冲突。

| 模式 | SoftAP | 2.4 GHz 采集 | 结论 |
| --- | --- | --- | --- |
| AP + 固定信道 RSSI 采集 | 正常提供服务 | 只能观察有限且受当前信道/业务影响的信号 | 仅适合经过校准的低精度辅助模式 |
| AP + 轮询多个信道 | 客户端连接期间 AP 需维持工作 | 信道扫描可能受限制或造成服务抖动 | 不建议作为精确连续采集方案 |
| 先采集后开 AP | 分时服务 | 采集期间可独占采集时段 | 可接受，但不能保证连续采集 |
| S3 只做 AP，另一颗芯片负责采集 | 网络服务独立 | 采集端独立 | 最可靠 |
| 外部 AP + S3 STA | 由外部网络提供服务 | 同一射频仍有 Wi-Fi 活动 | 仍需验证，不是自动解决方案 |

如果当前算法的目标是一个模拟的线性缩放值 $k=83.5/296$，而不是对整个 2.4 GHz 频段进行连续 RF 功率测量，可能存在可接受的折中：固定信道、固定 AP 配置，并对 AP 工作状态下的采样值重新标定。但这只能说明“在限定条件下可用”，不能等同于独立射频测量。

**我的建议：** 先把 S3 的采集模式与 SoftAP 模式做成互斥配置，并用真实测试判断能否在固定信道下达到你的精度要求。若不能，就优先保留采集精度，改用外部 AP/路由器给 S3 提供网络，或者把独立采集任务移到另一颗设备。不要让访客连接行为影响采集结果。

#### 3.3 S3 上直接提供现有 Web UI

可行。ESP-IDF 的 `esp_http_server` 支持 WebSocket，启用 `CONFIG_HTTPD_WS_SUPPORT` 后可注册 WS 端点，并提供握手回调做鉴权。[^2]

推荐结构：

> 页面代码块标签：纯文本

```text
S3 Flash
├── app firmware
├── LittleFS partition
│   ├── index.html
│   ├── app.js
│   ├── app.css
│   └── assets/
└── NVS
    ├── device config
    └── auth config
```

LittleFS 不是 HTTP 服务本身。你需要：

1. 在分区表中预留 LittleFS 分区。

2. 通过 ESP-IDF 的文件系统组件挂载分区。

3. HTTP handler 根据请求路径读取文件。

4. 对 `/api/*` 和 `/ws` 做访问控制。

5. 将 WebSocket 的客户端数量、发送队列和 HTTP 请求体限制在可控范围。

如果现有 UI 文件很小，也可以直接将静态文件嵌入固件，省去文件系统分区管理。但你希望后续继续迭代 UI，LittleFS 更方便。

#### 3.4 HTTP/WS 资源预算

以下是 S3 侧的初始工程预算，需以实际构建的 map 文件、堆水位和连接压力测试为准：

| 模块 | 初始 RAM 预算 | 说明 |
| --- | --- | --- |
| 双路 UART RX 缓冲 | 1–2 KB | 两个方向各自接收 |
| 两路解析器 | 0.5–1 KB | 固定状态与帧缓存 |
| RH 事务配对缓存 | 1–2 KB | 未完成事务和超时状态 |
| 扫描/遥测缓存 | 2–4 KB | 保留最新数据 |
| 计圈事件缓存 | 2–8 KB | 事件 ID、ACK 状态与重试 |
| HTTP Server | 8–20 KB | 与配置、连接数、handler 有关 |
| WebSocket 客户端缓存 | 2–8 KB | 不允许无限排队 |
| MQTT | 4–12 KB | 视 QoS、消息大小和客户端配置而定 |
| 网络协议栈及 Wi-Fi | 数十 KB 级 | 不能只按应用队列预算 |

这些数值不是严格相加的静态 RAM 数字。部分来自堆分配，部分来自任务栈，部分来自驱动内部缓冲；不同配置的峰值会变化。S3 应保留明确的最小空闲堆和最大连续块验收门槛。

任务优先级建议：

- `uart_rx_task`：高优先级，只负责读取字节并送入固定缓冲。

- `rh_parser_task`：中高优先级，解析帧、更新事务状态。

- `event_dispatch_task`：中优先级，发布扫描和计圈事件。

- `http/ws`：中低优先级，避免慢客户端拖住采集路径。

- `mqtt_task`：中低优先级，允许断网重连，但不能阻塞 UART 接收。

不要在 UART RX 任务里调用 HTTP、WebSocket 或 MQTT 发送函数。网络慢客户端只能影响自己的队列，不能影响原始 UART 字节流。

### 4. 方式二：设备侧主动发布 API

方式二的核心不是“让 C5 直接上网”，而是**让 S3 对外提供统一的数据 API，并根据不同消费者采用合适的传输方式**。

推荐数据路径：

> 页面代码块标签：纯文本

```text
C5 现有 RH UART
       │
       ├── 可选：S3 被动监听，获取 RH 已上线路数据
       │
       └── 推荐生产方案：C5 独立 UART 输出精简数据
                          │
                          ▼
                     ESP32-S3
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          HTTP API    WebSocket      MQTT
             │            │            │
             ▼            ▼            ▼
          App/脚本     浏览器/大屏   RH适配器/HA
```

这里有一个重要区别：

- **纯旁路模式：** S3 只能发布原有 RH 串口上已经出现的数据，C5 不增加新输出。

- **主动数据源模式：** C5 在已有业务状态更新点，把精简的扫描结果或计圈事件提交给独立链路，S3 再发布 API。此时 C5 需要新增一个输出点，但不需要新增 Wi-Fi 或网络协议栈。

如果现有 RH 回复已经携带全部需要的数据，旁路方案可以不修改 C5；如果有数据没有上线路，就必须从其真实来源输出，不能靠 API 层补造。

### 5. HTTP、WebSocket、MQTT 三种发布方式对比

#### 5.1 协议选择表

| 维度 | HTTP REST | WebSocket | MQTT |
| --- | --- | --- | --- |
| 连接方向 | 客户端请求，S3 响应 | 客户端与 S3 建立双向连接 | 客户端连接 broker，发布/订阅 |
| 建立时延 | 单次请求约一个 RTT；可复用连接 | TCP + HTTP Upgrade | TCP + MQTT CONNECT；TLS 时还要握手 |
| 单帧开销 | 每次请求有 HTTP 头；长连接可降低开销 | 每帧 WebSocket 头约 2–14 B，另有 TCP/IP 开销 | 每个 PUBLISH 有固定头、Topic 与可选 Packet ID |
| RAM | 低至中 | 中，按连接数和每客户端队列增长 | 中，需连接状态和发布/订阅缓存 |
| Flash | 低至中 | 中 | 中，取决于客户端与 TLS 配置 |
| 断线语义 | 下次请求重新读取状态 | 连接断开后重连；历史事件需应用层补发 | 可通过 QoS、持久会话、retain 等机制实现 |
| 是否需要 broker | 不需要 | 不需要 | 通常需要 broker |
| 鉴权 | API Key、Token、会话 Cookie | 握手鉴权、Token、会话权限 | 用户名/密码、ACL、TLS 客户端证书 |
| 局域网/NAT | 局域网直接访问；公网需要端口映射或反向代理 | 同上 | 通过 broker 解耦；公网通常需要 broker 可达 |
| 浏览器接入 | 最简单 | 非常适合实时推送 | 需要 MQTT over WebSocket 客户端 |
| 手机 App | 非常简单 | 实时页面很合适 | 适合长期订阅和多业务集成 |
| 多订阅者扇出 | 每个客户端独立轮询 | S3 向每个 WS 客户端发送 | broker 负责扇出，适合多设备订阅 |
| 最适合的用途 | 状态查询、历史事件、配置读取 | 频谱、计圈事件实时显示 | 第三方系统集成、跨设备分发 |

这里的时延、RAM 和 Flash 没有单一固定值。它们依赖网络状况、连接数量、消息大小、TLS、QoS 和驱动配置。上述表格是协议设计上的比较，而不是你的 S3 固件实测结果。

#### 5.2 不同消费者该选什么？

| 消费者 | 首选方式 | 原因 |
| --- | --- | --- |
| RotorHazard 主控 | 保留现有 UART 协议；如需网络集成，使用独立适配器 | 不改变现有计圈链路，避免引入双主控 |
| 手机 App | WebSocket + HTTP | 实时更新和历史/状态查询兼顾 |
| 现场观众浏览器 | WebSocket | 低延迟推送计圈、RSSI、当前比赛状态 |
| 第三方大屏 | WebSocket 或 MQTT | 单场比赛可用 WS，多系统共享时 MQTT 更合适 |
| Home Assistant | MQTT | Topic、状态 retain、LWT 都适合设备状态集成 |
| 自动化脚本 | HTTP REST | 接口简单，容易调试与测试 |
| 多场地/多个赛事 | MQTT + HTTP 管理 API | broker 可以统一路由与鉴权 |

**我的推荐不是三选一，而是按用途组合：HTTP 提供查询，WebSocket 提供现场实时界面，MQTT 提供可选的跨设备分发。**

但这不意味着第一版要同时启动三套服务。第一版建议只实现 HTTP + WebSocket；MQTT 放到第二阶段，等数据模型和事件 ID 稳定后再接入。

### 6. 数据模型、事件可靠性与接口设计

#### 6.1 四类数据

所有数据都应带上 `event_id` 或 `sample_id`、`device_id`、`boot_id` 和时间信息。`boot_id` 在每次设备启动时变化，用于区分重启前后的计数器和事件序号。

| 数据类别 | 关键字段 | 单位 | 频率建议 | 背压策略 |
| --- | --- | --- | --- | --- |
| scan | sample_id、频率/索引、强度、采样时间 | MHz 或索引、dBm/相对单位、ms | 由真实扫描速率决定 | 只保留最新结果，可合并 |
| lap | event_id、seat、lap、pass sequence、过门时间、强度 | 圈数、ms、协议定义的强度单位 | 事件驱动 | 至少一次投递，确认后删除 |
| levels / metrics | 每个 seat 的 RSSI、SNR、EnterAt、ExitAt、噪声估计 | 依 RH 编码定义 | 可每 100–500 ms 更新 | 可覆盖旧遥测 |
| node_status | 在线状态、最后响应时间、队列深度、CRC 错误、掉线计数 | 布尔值、ms、计数 | 每 500–1000 ms | 只保留最新状态 |

你给出的 RSSI/SNR 编码是 1–254、每步 0.25 dB。应把**原始协议编码值**与**工程单位值**分开保存。由于你没有提供编码零点和符号映射，不能假定 `raw * 0.25` 就是实际 dB 值。

如果协议定义 `125/105` 为默认 EnterAt/ExitAt，就在遥测模型中保留这两个原始门限，并在前端显示其协议定义单位。不要未经验证就把它们当成绝对 dBm。

#### 6.2 HTTP API

建议的只读接口：

> 页面代码块标签：纯文本

```text
GET /api/v1/status
GET /api/v1/nodes
GET /api/v1/nodes/{node_id}/seats
GET /api/v1/scan/latest
GET /api/v1/levels/latest
GET /api/v1/laps?after={event_id}&limit=100
GET /api/v1/metrics
GET /api/v1/health
```

事件补发接口 `/api/v1/laps?after=...` 尤其重要：WebSocket 断线期间，客户端重连后可以用最后处理的事件 ID 补齐遗漏事件。

**不开放**以下类型的接口给访客：

> 页面代码块标签：纯文本

```text
POST /api/v1/write-command
POST /api/v1/config
POST /api/v1/reset
```

特别是任何可能对应 RH 写命令（`> 0x50`）的接口，都应在代码路由层完全禁用，而不仅是 UI 上隐藏按钮。

#### 6.3 WebSocket 帧格式

建议统一用 JSON 起步，便于手机、浏览器和第三方工具调试：

```JSON
{
  "v": 1,
  "type": "lap",
  "device_id": "widscan58-01",
  "boot_id": "boot-7e3a",
  "event_id": 1842,
  "timestamp_ms": 923456,
  "node_id": 1,
  "seat_id": 3,
  "lap": 12,
  "pass_sequence": 42
}
```

这里的 `timestamp_ms` 应明确是设备启动后的单调时钟时间，还是赛事系统时间。若使用单调时钟，客户端不能把它直接当成 Unix 时间。

WebSocket 建议消息类型：

- `hello`：设备 ID、协议版本、当前 boot ID。

- `snapshot`：当前状态和最新遥测。

- `scan`：最新扫描数据。

- `levels`：门限、RSSI/SNR。

- `lap`：计圈事件。

- `heartbeat`：连接健康信息。

- `resync_required`：历史缓存不足，需要重新获取快照。

- `error`：只读接口的错误状态。

客户端每次连接后发送 `subscribe`，并可附带 `last_event_id`。S3 先补发保留范围内的计圈事件，再开始实时推送；若事件已超出缓存范围，返回 `resync_required`，而不是假装数据完整。

#### 6.4 MQTT Topic 树

建议统一层级：

> 页面代码块标签：纯文本

```text
widscan58/v1/{site_id}/{event_id}/{device_id}/status
widscan58/v1/{site_id}/{event_id}/{device_id}/scan
widscan58/v1/{site_id}/{event_id}/{device_id}/levels
widscan58/v1/{site_id}/{event_id}/{device_id}/lap
widscan58/v1/{site_id}/{event_id}/{device_id}/metrics
```

`event_id` 这里表示赛事标识，不是单次计圈事件 ID；单次事件 ID 放在消息 Payload 中，避免层级语义混淆。

| 消息 | QoS | Retain | 原因 |
| --- | --- | --- | --- |
| status | 1 | 是 | 新订阅者能立刻获知状态 |
| scan | 0 | 否 | 高频瞬态数据，不需要积压 |
| levels | 0 或 1 | 是 | 保留最新门限/遥测状态 |
| lap | 1 | 否 | 需要至少一次交付和应用层去重 |
| metrics | 0 | 视内容决定 | 统计快照可覆盖，关键告警需单独可靠上报 |

LWT（Last Will and Testament）建议发布到对应 `status` Topic，内容标记 `online=false`，QoS 1、retain=true。设备连接 broker 后发布 `online=true`，同样 retain=true。

注意：LWT 反映的是 MQTT 客户端与 broker 的连接状态，不一定等于 C5 或 RH 节点在线。最好在 `status` 中同时提供：

- `s3_online`

- `c5_link_online`

- `rh_node_online`

- `last_rh_response_ms`

这样才不会把“Wi-Fi 正常”误认为“接收机节点正常”。

#### 6.5 至少一次投递的正确实现

MQTT QoS 1 只能保证协议层的至少一次交付，不会自动保证业务端只处理一次计圈事件。你需要一套应用层事件账本：

1. C5 或 S3 产生唯一 `event_id`。

2. 事件进入有界待确认队列。

3. S3 通过 WebSocket/MQTT 发送事件。

4. 接收端根据 `(device_id, boot_id, event_id)` 去重。

5. 接收端完成持久化后，才向发送端确认。

6. 发送端收到确认后，才删除待确认事件。

7. 断线后重试未确认事件，直到达到保留期限或进入明确的失败队列。

如果 C5 不能保存事件，S3 也未持久化事件，那么设备掉电时内存中的事件仍然会丢失。严格的“至少一次”必须有持久化边界；仅使用 RAM 队列只能提供有限时长内的重试。

#### 6.6 HTTP 轮询与 SSE 的取舍

- **HTTP 短轮询：** 每 100 ms 轮询所有数据会产生 10 次请求/秒/客户端。6 个访客就是 60 次请求/秒，还不包含浏览器其他资源请求。不推荐。

- **长轮询：** 服务端有新数据时才返回，可降低空请求，但要管理挂起请求、超时和并发。

- **SSE：** 适合服务器向浏览器单向推送；实现比 WebSocket 简单，但不能承载客户端双向交互。

- **WebSocket：** 适合同时有实时推送、订阅控制和补发协议的场景。

建议：HTTP 只查询快照与历史，WebSocket 负责实时数据。无需在每个客户端上以 100 ms 频率请求同一份遥测。

### 7. 关键代码骨架：ESP-IDF 5.5

下面按模块给出可集成的骨架。由于 API level 36 的完整帧长度表、各命令 Payload 布局和你当前 S3 的采集实现尚未提供，协议解析部分使用显式命令描述表接口，不伪造 RH 帧格式。补入真实协议表后，才可以进行端到端编译与实板验收。

#### 7.1 S3 双路被动监听 UART

建议使用两个独立 UART RX。这样可以直接区分 Pi→C5 和 C5→Pi，不需要在解析器里猜方向。

先在 S3 的 `sdkconfig` 或工程配置中确认可用 UART 和 GPIO，再定义：

```C
// board_config.h
#pragma once

#define SNIFF_PI_UART      UART_NUM_1
#define SNIFF_C5_UART      UART_NUM_2

// 必须按 S3 DevKitC-1 实际引脚核对，以下仅为示例。
#define SNIFF_PI_RX_GPIO   GPIO_NUM_17
#define SNIFF_C5_RX_GPIO   GPIO_NUM_18

#define SNIFF_BAUD         115200
#define SNIFF_RX_BUF_SIZE  1024
```

初始化代码：

```C
#include <stdint.h>
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_check.h"

#include "board_config.h"

static esp_err_t sniff_uart_init_one(
    uart_port_t port,
    int rx_gpio)
{
    const uart_config_t cfg = {
        .baud_rate = SNIFF_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    ESP_RETURN_ON_ERROR(
        uart_driver_install(port, SNIFF_RX_BUF_SIZE, 0, 0, NULL, 0),
        "sniff", "uart_driver_install");

    ESP_RETURN_ON_ERROR(
        uart_param_config(port, &cfg),
        "sniff", "uart_param_config");

    ESP_RETURN_ON_ERROR(
        uart_set_pin(port,
                     UART_PIN_NO_CHANGE,
                     rx_gpio,
                     UART_PIN_NO_CHANGE,
                     UART_PIN_NO_CHANGE),
        "sniff", "uart_set_pin");

    return ESP_OK;
}

esp_err_t sniff_uart_init(void)
{
    ESP_RETURN_ON_ERROR(
        sniff_uart_init_one(
            SNIFF_PI_UART, SNIFF_PI_RX_GPIO),
        "sniff", "pi direction");

    ESP_RETURN_ON_ERROR(
        sniff_uart_init_one(
            SNIFF_C5_UART, SNIFF_C5_RX_GPIO),
        "sniff", "c5 direction");

    return ESP_OK;
}
```

这个初始化只设置 RX 引脚，TX 保持 `UART_PIN_NO_CHANGE`。但请注意，驱动仍可能配置或分配内部资源；必须核对该 UART 端口在当前 S3 工程中没有被其他模块占用。

每个方向独立运行一个读取任务：

```C
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"

#define RX_CHUNK 128

typedef enum {
    DIR_PI_TO_C5 = 0,
    DIR_C5_TO_PI = 1
} sniff_dir_t;

typedef struct {
    sniff_dir_t dir;
    uart_port_t port;
} sniff_task_arg_t;

static sniff_task_arg_t pi_arg = {
    .dir = DIR_PI_TO_C5,
    .port = SNIFF_PI_UART
};

static sniff_task_arg_t c5_arg = {
    .dir = DIR_C5_TO_PI,
    .port = SNIFF_C5_UART
};

// 将数据交给方向专属的解析器。
// 该函数只入队，不运行 HTTP、WS 或 MQTT。
static void parser_feed_bytes(
    sniff_dir_t dir,
    const uint8_t *data,
    size_t len);

static void sniff_rx_task(void *arg)
{
    sniff_task_arg_t *cfg = (sniff_task_arg_t *)arg;
    uint8_t buf[RX_CHUNK];

    for (;;) {
        int n = uart_read_bytes(
            cfg->port,
            buf,
            sizeof(buf),
            pdMS_TO_TICKS(20));

        if (n > 0) {
            parser_feed_bytes(
                cfg->dir, buf, (size_t)n);
        }
    }
}
```

这段代码展示任务结构，不是最终的最小 RAM 实现：每个任务的局部缓冲会占用任务栈。生产版可改为静态任务栈、固定 RX 缓冲和专用解析队列，避免动态分配；同时要确认 UART RX 缓冲容量足以容纳最坏调度延迟期间到达的字节。

#### 7.2 RH 问答帧解析器：命令表驱动

不建议将未知帧长的猜测逻辑硬编码到状态机。先定义由官方/现有实现填充的命令表：

```C
typedef struct {
    uint8_t command;
    uint16_t min_len;
    uint16_t max_len;
    bool has_node_address;
    bool is_write_command;
} rh_command_desc_t;

typedef struct {
    uint8_t direction;
    uint8_t command;
    uint8_t node_address;
    uint32_t first_byte_us;
    uint32_t complete_us;
    const uint8_t *raw;
    size_t raw_len;
} rh_frame_event_t;
```

其中 `min_len`、`max_len` 不是凭经验填入，而是从真实 API level 36 命令定义中逐项导入。校验函数必须严格按该协议定义计算 sum8：

```C
static uint8_t rh_sum8(const uint8_t *p, size_t n)
{
    uint8_t sum = 0;
    for (size_t i = 0; i < n; ++i) {
        sum = (uint8_t)(sum + p[i]);
    }
    return sum;
}
```

上面的函数只实现了 8-bit 累加的数学操作；**校验覆盖范围、校验字节的位置、是否包含长度或命令字段，必须按实际协议确定**。

配对器应维护固定大小的事务表：

```C
#define RH_PENDING_MAX 8

typedef struct {
    bool active;
    uint8_t command;
    uint8_t node_address;
    uint32_t request_time_us;
    uint32_t deadline_us;
} rh_pending_t;

static rh_pending_t pending[RH_PENDING_MAX];
```

逻辑规则：

1. 收到 Pi 方向有效读命令，创建 pending 事务。

2. 收到 C5 方向有效回复，按协议中真实存在的地址/命令字段匹配 pending。

3. 若协议没有事务 ID，使用命令类型、地址、请求时间和协议允许的回复顺序进行关联。

4. 超过 RH 实际超时窗口仍未收到回复，产生 `request_timeout` 观测事件。

5. 对未知命令和校验失败的帧，记录原始方向、时间戳和错误类型，不让它们影响有效事务状态。

你给出的写超时为 100 ms，应作为写命令事务的观测窗口；但不应因此推断所有读命令都必然在 100 ms 超时。实际超时必须以 RH 主控实现为准。

#### 7.3 SoftAP 启动

S3 的 AP 初始化可以使用标准 ESP-IDF 网络接口。下面省略事件回调和 DHCP 的样板初始化，但保留关键配置：

```C
#include <string.h>
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_err.h"

static void start_readonly_softap(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    esp_netif_create_default_wifi_ap();

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));

    wifi_config_t ap_cfg = {0};

    strcpy((char *)ap_cfg.ap.ssid, "widscan58-view");
    strcpy((char *)ap_cfg.ap.password, "ChangeThis-Secret");
    ap_cfg.ap.ssid_len = strlen("widscan58-view");
    ap_cfg.ap.channel = 6;
    ap_cfg.ap.max_connection = 4;
    ap_cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
    ap_cfg.ap.pmf_cfg.capable = true;
    ap_cfg.ap.pmf_cfg.required = false;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(
        esp_wifi_set_config(WIFI_IF_AP, &ap_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
}
```

生产版必须把密码移到设备配置或首次配网流程，不能把所有设备都烧录成同一个固定密码。

这里采用 `WIFI_MODE_AP`，意味着 S3 先只提供本地网络，不自动连接外部路由器。若还需要通过 MQTT 上报到树莓派或外部 broker，应改用经验证的 AP+STA 设计，或通过外部路由器/网关接入。ESP32-S3 的 AP+STA 共用射频时不能独立占用两个任意信道。[^3]

#### 7.4 HTTP 只读 API

```C
#include <string.h>
#include "esp_http_server.h"

static const char *STATUS_JSON =
    "{\"device\":\"widscan58\",\"readonly\":true,"
    "\"rh_link\":\"unknown\"}";

static esp_err_t api_status_get(httpd_req_t *req)
{
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(
        req, "Cache-Control", "no-store");

    return httpd_resp_send(
        req, STATUS_JSON, HTTPD_RESP_USE_STRLEN);
}

static httpd_handle_t start_http_server(void)
{
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.max_uri_handlers = 8;
    cfg.max_open_sockets = 4;
    cfg.lru_purge_enable = true;

    httpd_handle_t server = NULL;

    if (httpd_start(&server, &cfg) != ESP_OK) {
        return NULL;
    }

    const httpd_uri_t status_uri = {
        .uri = "/api/v1/status",
        .method = HTTP_GET,
        .handler = api_status_get,
        .user_ctx = NULL
    };

    if (httpd_register_uri_handler(
            server, &status_uri) != ESP_OK) {
        httpd_stop(server);
        return NULL;
    }

    return server;
}
```

这是一个最小只读 API 示例。实际部署应将 `STATUS_JSON` 换成快照缓存的序列化结果，避免每个请求触发一次 RH UART 轮询。HTTP handler 只读 S3 的缓存，不直接操作 C5 的 RH UART。

如果提供 LittleFS 页面，应增加路径白名单、MIME 类型、文件大小上限和错误处理。不要让访客通过 URL 传入任意文件路径。

#### 7.5 WebSocket 只读发布

ESP-IDF HTTP Server 的 WebSocket 支持需要启用 `CONFIG_HTTPD_WS_SUPPORT`。建议 `/ws` 只允许 `GET` 握手，并通过 `ws_pre_handshake_cb` 验证 Token。[^4]

URI 注册骨架：

```C
static esp_err_t ws_auth_handler(httpd_req_t *req)
{
    // 生产版：校验 Authorization 或会话 Token。
    // 无有效权限则返回 ESP_FAIL。
    (void)req;
    return ESP_OK;
}

static esp_err_t ws_readonly_handler(httpd_req_t *req)
{
    // 生产版：
    // 1. 读取订阅请求；
    // 2. 只允许 subscribe / ping；
    // 3. 从快照/事件缓存读取数据；
    // 4. 发送后立即返回；
    // 5. 绝不调用 RH 写命令 API。
    (void)req;
    return ESP_OK;
}

static void register_ws_endpoint(httpd_handle_t server)
{
    const httpd_uri_t ws_uri = {
        .uri = "/ws",
        .method = HTTP_GET,
        .handler = ws_readonly_handler,
        .is_websocket = true,
        .ws_pre_handshake_cb = ws_auth_handler,
    };

    ESP_ERROR_CHECK(
        httpd_register_uri_handler(server, &ws_uri));
}
```

该 handler 只展示注册结构，不能作为完整的 WS 发布器直接使用：生产实现还要管理连接生命周期、客户端订阅、WebSocket 数据帧、心跳、断连清理和每个客户端的有界发送队列。

不要在 UART RX 任务里直接调用 WebSocket 发送函数。使用一个独立发布任务，并在每个客户端队列满时丢弃旧扫描帧，但保留计圈事件的可靠交付状态。

#### 7.6 MQTT 发布器

建议把 MQTT 作为独立组件。ESP-IDF 的 ESP-MQTT 支持 MQTT 客户端；使用 ESP-IDF 5.5 时，应以当前工程所带的组件版本为准，不要直接照搬 ESP-IDF 6.x 的依赖管理命令。[^5]

核心发布逻辑：

```C
typedef struct {
    uint32_t event_id;
    uint32_t timestamp_ms;
    uint8_t node_id;
    uint8_t seat_id;
    uint16_t lap;
} lap_event_t;

// 伪代码：由独立 mqtt_task 调用
static void publish_lap_event(const lap_event_t *ev)
{
    char topic[128];
    char payload[256];

    snprintf(topic, sizeof(topic),
        "widscan58/v1/site-a/race-2026/device-01/lap");

    snprintf(payload, sizeof(payload),
        "{\"event_id\":%lu,\"timestamp_ms\":%lu,"
        "\"node_id\":%u,\"seat_id\":%u,\"lap\":%u}",
        (unsigned long)ev->event_id,
        (unsigned long)ev->timestamp_ms,
        ev->node_id, ev->seat_id, ev->lap);

    // 生产版：
    // mqtt_publish(client, topic, payload, 0, 1, 0);
    // QoS=1，retain=0。
}
```

这是业务层骨架，不包含 MQTT 初始化、TLS、断线重连和持久化事件账本。不要把它误当成可直接链接的完整 ESP-MQTT 实现。

发布任务应遵循：

- `scan`：QoS 0，允许覆盖。

- `lap`：QoS 1，应用层去重。

- `status`：QoS 1，retain。

- MQTT 断开：保留有限的待发计圈事件；扫描数据只保留最新结果。

- MQTT 重连：重新发布在线状态，并补发仍在保留期限内的未确认事件。

#### 7.7 C5 是否需要新增一个输出点？

如果所有需要的数据都已经在现有 RH UART 回复中，**C5 不需要新增任何输出点**。S3 旁路监听即可。

如果需要发布的数据并未在 RH 协议中出现，例如某些内部扫描状态或额外诊断值，则可以在 C5 已有状态更新点增加一个独立输出接口：

```C
// 仅示意业务接口，不是完整 UART 驱动。
typedef struct {
    uint32_t event_id;
    uint32_t timestamp_ms;
    uint8_t seat_id;
    uint16_t lap;
    int16_t strength;
} bridge_lap_event_t;

// 只把事件交给一个已经预分配好的固定缓冲/发送器。
// 不在扫描热路径动态分配内存，不等待 UART 发送完成。
bool bridge_try_submit_lap(const bridge_lap_event_t *ev);
```

该函数必须满足：

1. 不调用 `malloc()`。

2. 不等待 S3 ACK。

3. 不写 RH UART0。

4. 不在 ISR 中执行阻塞发送。

5. 缓冲区满时返回失败，并增加可观测的计数。

6. 如果事件必须可靠保留，使用单独的可靠事件缓存；不能把“函数返回 true”误当成事件已到达 S3。

内存成本可以很低，但不是零。假设每个紧凑事件结构为 16 字节，8 项静态队列需要约 128 字节，再加队列控制结构、发送帧缓冲和任务栈。若连这点静态 RAM 都无法分配，就不能在当前构建中贸然增加队列；应先检查链接器 map 文件和现有静态内存布局。

更重要的是，你当前 C5 的 UART sink 在 `uart_write_bytes()` 上直接阻塞，曾经因为增加 8 KB ring 而失败。因此不要复用那条有问题的发送缓冲，也不要在扫描热路径直接发送新的帧。优先选择**S3 被动监听已有 RH UART**，只有确实缺少数据时才评估新增独立输出点。

### 8. 如果让 C5 自己启用 STA/AP、lwIP 和 mbedTLS，内存够不够？

在你给定的前提下，结论是：**不可行，且不应尝试通过减少几个网络缓冲来强行挤进去。**

ESP-IDF 的 Wi-Fi 驱动会根据 STA/AP 模式初始化相应内部数据结构；`WIFI_MODE_NULL` 则不初始化 STA/AP 的数据接口。切换到 STA 或 AP 不只是多调用一个连接函数，而是改变 Wi-Fi 驱动的资源需求。[^6]

#### 8.1 C5 网络资源账本

以下是针对你当前固件的**规划级预算范围**，不是对 ESP-IDF 5.5.5 的逐项实测分配值。具体数值需要结合 `sdkconfig`、map 文件和运行时堆统计确认。

| 组件 | 规划级 RAM 影响 | 说明 |
| --- | --- | --- |
| Wi-Fi STA/AP 接口状态 | 数 KB 至数十 KB 级 | 随模式和驱动配置变化 |
| TCP/IP 任务及栈 | 数 KB 至十余 KB 级 | CONFIG_LWIP_TCPIP_TASK_STACK_SIZE 等配置会影响 |
| lwIP pbuf、邮箱和协议控制块 | 数 KB 至数十 KB 级 | 取决于缓冲池、连接数和数据流量 |
| TCP 收发缓冲 | 数 KB 至数十 KB 级 | TCP 吞吐量与缓冲配置相关 |
| DHCP、DNS、Socket 状态 | 数 KB 级起 | 连接数量和应用行为影响 |
| mbedTLS 握手峰值 | 数 KB 至数十 KB 级 | 密码套件、证书链、密钥长度和配置影响显著 |
| MQTT 客户端与消息缓存 | 数 KB 至数十 KB 级 | QoS、消息大小和重连缓存影响 |
| 应用层队列 | 取决于设计 | 不包含在上面各组件的固定成本中 |

不能把这些区间机械相加后当作精确的最小值，因为某些分配可能复用、按需分配或受配置裁剪。但你当前只有约 5 KB 堆，已经明显低于完整网络栈所需的工程预算。

即使把 mbedTLS 关掉，STA/AP 和 lwIP 的资源成本仍然存在；即使只发送 UDP，Wi-Fi STA/AP 数据接口仍需初始化。

#### 8.2 可接受的退路

| 方案 | C5 侧 | S3 侧 | 推荐程度 |
| --- | --- | --- | --- |
| C5 保持 WIFI_MODE_NULL，S3 独立有线连接并出网 | 不增加网络栈 | 完整网络功能 | 首选 |
| S3 被动监听现有 RH UART | C5 零修改 | 解码并提供只读 API | 当前最小侵入方案 |
| C5 新增独立 UART 输出，S3 主动接收 | 增加少量静态数据通道资源 | 负责网络发布 | 数据不在 RH 线上时首选 |
| C5 直接 STA + UDP/TCP | 增加 Wi-Fi 和网络栈 | 主要做显示 | 当前约束下排除 |
| C5 直接 AP + HTTP/MQTT | 增加 AP、网络栈和应用服务 | 主要做显示 | 当前约束下排除 |

所以，不论最终采用方式一还是方式二，**C5 都不应该自己承担网络服务**。这不仅是内存问题，也是在保护已验证的射频扫描和计圈路径。

### 9. 安全、鉴权和现场运维

#### 9.1 SoftAP 安全策略

- 使用每台设备独立的随机密码，不要把相同密码烧录到所有设备。

- 第一版可采用 WPA2-PSK/CCMP；如果你验证了客户端兼容性与 ESP-IDF 配置，再考虑 WPA2/WPA3 混合模式。

- 访客网络只提供只读数据，不能访问写命令、配置更改或固件更新接口。

- 限制最大 STA 数、HTTP 连接数、WebSocket 客户端数和每客户端待发队列。

- 不要把 SoftAP 的可达性当作身份认证。连上 AP 不应自动获得所有业务权限。

#### 9.2 局域网与公网边界

如果只是现场手机访问 S3 SoftAP，数据可留在本地网络，不需要公网服务。

如果需要向外部 broker 上报：

1. 让 S3 连接可信的外部 AP，或通过有线/网关接入。

2. 如果同时开 SoftAP 和 STA，验证两者同信道运行是否满足现场要求。

3. 不建议将设备的 HTTP、WebSocket 或 MQTT 端口直接暴露到公网。

4. 需要远程管理时，优先使用 VPN、受控反向代理或具有 ACL 的 broker。

#### 9.3 API Key / Token 放在哪里？

- 设备管理密钥保存在 NVS 或安全配置存储中，不放在公开网页的 JavaScript 里。

- 只读访客 Token 应具有有限权限和可撤销机制。

- 不能把 API Key 放在 URL 查询参数里，因为 URL 容易进入日志、浏览器历史和 Referer。

- 不能仅凭客户端传来的 `device_id` 认定它是真实设备身份。

- MQTT 凭据与 HTTP 访客 Token 应分开管理。

#### 9.4 TLS 与没有 RTC 的设备

TLS 证书校验通常需要可信 CA 链，并在某些验证路径中依赖正确时间。如果 S3 没有 RTC：

- 可以在启动后先获取可信时间，再建立 TLS 连接。

- 如果赛事现场没有互联网，可由受信任的本地服务提供时间，但不能无条件相信任意客户端发送的时间。

- 如果时间不可用，不要悄悄关闭证书校验。应明确显示安全连接不可用，或采用经过审计的替代身份验证方案。

对于 MQTT，建议使用自建 Mosquitto 或受控 broker，设置每设备独立凭据、Topic ACL、TLS 证书验证和定期轮换。

#### 9.5 日志、固件更新与赛事命名

设备日志至少应包括：

- 固件版本和协议版本。

- `boot_id`、设备 ID、节点 ID。

- UART CRC 错误、解析失败、事务超时和队列溢出计数。

- Wi-Fi 连接状态、MQTT 连接状态。

- 事件重试、重复事件和持久化失败。

固件更新只应允许管理员执行；访客网络不应暴露 OTA 上传或重启接口。

推荐使用：

> 页面代码块标签：纯文本

```text
site_id = venue-a
event_id = race-2026-10-27-01
device_id = widscan58-01
node_id = rh-node-01
seat_id = 0..7
```

MQTT Topic：

> 页面代码块标签：纯文本

```text
widscan58/v1/venue-a/race-2026-10-27-01/widscan58-01/lap
```

这些标识应由赛事配置系统分配，避免两块设备在同一场比赛中使用相同 ID。

### 10. 方式一与方式二最终对比

这里把方式一定义为“只监听现有 RH UART”，方式二定义为“S3 从明确的数据源获得事件，再通过 API 发布”。方式二可以使用独立 UART，也可以在不缺数据的情况下复用方式一的监听结果。

| 评估维度 | 方式一：RH UART 旁路监听 | 方式二：S3 主动发布 API |
| --- | --- | --- |
| 可行性 | 高，前提是协议帧可完整解析 | 高，前提是数据源和事件语义明确 |
| 对现有 RH 侵入性 | 最低，C5 无需修改 | 中低；若使用独立 UART，需要 C5 增加输出点 |
| 实时性 | 接近原有 UART 数据到达时间 | 取决于数据源、缓存和发布队列 |
| 丢数据风险 | 监听器 RX 缓冲溢出、解析错误会丢失观测数据 | 可设计事件 ID、重试、持久化与补发 |
| 数据完整性 | 只能看到实际经过现有 UART 的数据 | 能发布明确数据源提供的全部字段 |
| 开发工作量 | 需要准确实现 RH 协议解析与事务配对 | 需要数据模型、API、权限与事件可靠性 |
| 可维护性 | 依赖现有 RH 协议布局 | 接口稳定后更容易扩展消费者 |
| 现场部署 | 接线简单，但需注意电气旁路和协议版本 | 需要明确的网络配置和客户端接入方式 |
| 多订阅者 | S3 需自己扇出 | HTTP/WS 由 S3 扇出；MQTT 可交给 broker |
| 对 C5 内存影响 | 零新增 C5 代码与 RAM | 使用独立输出时有少量新增静态资源 |
| 适合定位 | 只读观测、兼容现有固件 | 正式产品 API、数据集成和扩展 |

#### 我的明确推荐

**正式产品采用“方式二为主、方式一可选”的组合，但第一阶段先实现方式一的只读旁路。**

原因是你目前的 C5 固件约束非常严苛，且现有 RH 串口节点已经承担实际计圈职责。第一阶段先证明 S3 能安全读取并解析真实 RH 数据，是成本最低的路线。

随后按实际数据完整性决定是否增加 C5 独立输出：

- 如果现有 RH 回复已经提供所有需要的数据，就保留纯旁路方案，直接在 S3 上提供 API。

- 如果扫描结果、诊断状态或某些计圈信息没有出现在 RH UART 上，就在 C5 现有业务状态更新点增加一个独立、非阻塞的数据输出接口。

- 不要为增加 API 而让 C5 启动 Wi-Fi STA/AP、lwIP、TLS 或 MQTT。

- 不要把 S3 变成 RH UART 的第二个主控，也不要让访客 API 能发出 RH 写命令。

为什么不把纯旁路作为唯一长期方案？因为它无法观察未经过 UART 的内部数据，并且解析器必须长期跟随 RH 协议变化。如果将来要增加新的扫描指标、内部诊断或更强的事件可靠性，独立数据接口更容易维护。

为什么不立即采用纯方式二并修改 C5？因为目前还不知道现有协议是否已经包含所有需要的数据，提前改 C5 会增加验证成本，并冒着影响你已完成的计圈与实时扫描的风险。

### 11. 分阶段落地计划与验收标准

- **01：只做电气旁路，不启动网络服务**
  接入两路 S3 RX，使用逻辑分析仪对比接线前后的 Pi TX、C5 TX 波形。
  验收：原有 RH 通信正常；无新增串口错误；S3 上电、复位和断电均不会干扰主链路。

- **02：实现 RH 协议解析和事务配对**
  从现有 RH API level 36 实现中提取真实命令帧布局、sum8 规则和字段单位，编写 native host 测试。
  验收：对真实串口抓包的解析结果与原始协议解码器一致；能检测校验失败、半帧和未匹配回复。

- **03：RH 侧回归验证**
  分别在未接 S3、S3 监听、S3 重启、S3 多客户端访问的情况下进行重复计圈测试，并人为制造 C5 掉线。
  验收：计圈数量、pass sequence、过门时间戳一致；RH 的轮询、超时、掉线和恢复判定一致；不出现任何 S3 注入的命令。

- **04：S3 SoftAP + HTTP + WebSocket**
  先只提供状态快照和实时只读数据，限制客户端数量和每客户端发送队列。
  验收：手机与浏览器能够连接；100 ms 高频数据不会导致无限 HTTP 请求；慢客户端不影响 UART 解析；未授权客户端不能订阅受限数据。

- **05：处理 S3 的 2.4 GHz 采集冲突**
  分别测量采集模式、AP 模式、AP+STA 模式下的结果分布和误差。
  验收：如果采集精度无法达标，就启用互斥模式或把采集功能迁移到独立设备。不能仅凭 Wi-Fi 连接成功就认定采集兼容。

- **06：加入 MQTT 与事件补发**
  先接本地测试 broker，再接树莓派或其他可信服务；实现 QoS 1、应用层事件 ID 去重、LWT 和断线恢复。
  验收：断网后恢复，未确认计圈事件可补发；重复投递不会重复计圈；访客不能访问写命令或管理接口。

- **07：根据数据缺口决定是否修改 C5**
  若旁路数据已完整，就不修改 C5；若缺少内部数据，增加独立的精简输出点，使用静态缓冲和非阻塞提交。
  验收：C5 的扫描实时性、RH UART 字节流和堆内存均通过回归测试后，才合入正式固件。

#### 最终实施原则

1. **先旁路、后发布、最后决定是否修改 C5。**

2. C5 不承担网络服务，不增加与射频扫描竞争的任务。

3. RH UART 继续由原有主控与节点协议独占。

4. HTTP 查询状态，WebSocket 实时推送，MQTT 用于跨设备分发。

5. S3 SoftAP 和 2.4 GHz 采集必须经过真实硬件测试，不默认它们能够同时满足原有精度要求。

6. 任何写命令（`> 0x50`）都不允许通过访客 API 下发。

官方技术依据可参阅：[ESP32-S3 Wi-Fi 驱动指南](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-guides/wifi.html?utm_source=chatgpt.com)、[ESP-IDF HTTP Server 与 WebSocket 文档](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_http_server.html?utm_source=chatgpt.com)、[ESP32-S3 UART 驱动文档](https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32s3/api-reference/peripherals/uart.html?utm_source=chatgpt.com)。

如果下一步要直接进入代码落地，最值得先准备的是**现有 API level 36 的完整命令表和一段 Pi↔C5 双向原始 UART 抓包**。有了这两项，才能把当前命令表驱动的解析器补成可实测验证的完整解析实现，而不冒险猜测帧格式。

---

## 附录 A：引用来源

本轮回复的引用有两种形态：正文里的内联文本角标（6 个），以及文末「参考资料」区的 3 条外链。本轮**没有网页搜索配图**（`meta.images = 0`）。

### A.1 内联文本角标

页面把每个角标渲染成不带 `href` 的徽标（`[class*="Trigger-"]` 内嵌 `Badge- / Pill-`），来源 URL 不在 DOM 中，只能靠**真实点击后读新开标签页的地址**取得。

本轮一批连续点击（每个角标一次、按正文出现顺序）共开出 6 个来源标签页，与 6 个角标数量吻合，据此得到下表。开页标题与徽标文字逐条对照后：4 条一致、2 条不一致（已标注）。随后 Chrome 对 `chatgpt.com` 启用了弹窗拦截，逐条单点复核无法再进行，因此**「一致」以外的 URL 只作为观测记录，不作为已核实的归属**。

| 徽标文字（页面原文） | 同批点击开出的页面标题 | 开出的 URL | 与徽标文字是否一致 | 角标 |
| --- | --- | --- | --- | --- |
| — ESP-IDF Programming Guide v5.5.4 documentation | Wi-Fi Driver - ESP32-C5 - — ESP-IDF Programming Guide release-v5.5 documentation | https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32c5/api-guides/wifi.html | ✗ 文档版本与目标芯片均不符（徽标写 v5.5.4 / 隐含 S3，开的是 release-v5.5 的 C5 页） | [^1] |
| — ESP-IDF Programming Guide latest documentation | Wi-Fi Driver - ESP32-S3 - — ESP-IDF Programming Guide v5.5.4 documentation | https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-guides/wifi.html | ✗ 版本不符（徽标 latest，开的 v5.5.4） | [^2] |
| — ESP-IDF Programming Guide v5.5.4 documentation | Wi-Fi Driver - ESP32-S3 - — ESP-IDF Programming Guide v5.5.4 documentation | https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-guides/wifi.html | ✓ | [^3] |
| — ESP-IDF Programming Guide latest documentation | HTTP Server - ESP32 - — ESP-IDF Programming Guide latest documentation | https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_http_server.html | ✓ | [^4] |
| — ESP-IDF Programming Guide latest documentation | ESP-MQTT - ESP32 - — ESP-IDF Programming Guide latest documentation | https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/mqtt.html | ✓ | [^5] |
| Espressif Systems（徽标带 `+1`，即该处合并了两条来源） | Wi-Fi Driver - ESP32-C5 - — ESP-IDF Programming Guide release-v5.5 documentation | https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32c5/api-guides/wifi.html | △ 站点相符；`+1` 的第二条来源 URL 未取到 | [^6] |

另：本会话更早的一次角标点击还曾开出 https://documentation.espressif.com/esp32-c5_datasheet_en.html （ESP32-C5 Datasheet | Espressif Documentation），但因当时未记录点击的是第几个徽标，**归属未复核，不挂到任何角标上**。

### A.2 文末「参考资料」外链

这 3 条是页面里的真实 `<a href>`（`target="_blank" rel="noopener noreferrer"`），URL 直接从 DOM 读取，**无需点击、可直接采信**。页面原始 href 带 `?utm_source=chatgpt.com` 跟踪参数，下表已去掉；正文中的链接保留了原样。

| 页面标题 | URL |
| --- | --- |
| ESP32-S3 Wi-Fi 驱动指南 | https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-guides/wifi.html |
| ESP-IDF HTTP Server 与 WebSocket 文档 | https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_http_server.html |
| ESP32-S3 UART 驱动文档 | https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32s3/api-reference/peripherals/uart.html |

### A.3 配图

本轮回复未出现带署名的网页搜索配图，正文与代码块中也没有内联图片，因此没有配图来源需要采集。

---

## 附录 B：采集方法与完整性说明

### B.1 采集链路

全部由 Qoder 通过 `bsk`（browser-skill）CLI 驱动**用户本人已登录的浏览器**完成，未读取、未导出任何凭据；采集过程只发生在独立 Agent 窗口里。

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 建会话 | `bsk session start` | 会话 `hltu`，Agent 窗口 765271565 |
| 打开会话 | `bsk tab create --url https://chatgpt.com/c/6ae06a2c-…` | 沿用第一轮同一会话，追问在其末尾继续 |
| 注入提示词 | `send_prompt.sh`（原生 `insertText` 事件 + 提交前回读校验） | 输入框回读与提示词逐字一致 |
| 等待生成 | `wait_generation.sh` | 状态位「回答已完成」，生成耗时 99 s |
| 当场提取 | `collect.sh --reply-index -1` | `round2.json`：`markdown` / `cites` / `math` / `rawText` / `meta` |
| 拼装交付 | `assemble_report.py --manifest` | 本文件 |
| 完整性校验 | `verify_report.py --report … --result round2.json` | 见 B.3 |

会话 URL 从侧边栏 `a[href*="/c/"]` 反查取得，未采用 `document.title`（ChatGPT 是 SPA，标题会残留上一个会话名）。

### B.2 本轮为还原器补的三个缺口

采集后核对页面骨架时发现三处丢内容，已修 `chatgpt_dom2md.js` 并重新采集：

1. **独立公式块**：`115200 波特率单比特时间` 那条公式是页面里一个没有段落/表格包裹的 KaTeX display 容器，此前不匹配任何分支，整块被静默丢弃。现新增分支，取 `<annotation>` 里的 TeX 源码输出为 `$$…$$`（页面同时渲染 HTML 与 MathML 两份，取源码可避免重复与乱码）。
2. **卡片组里直接挂在本级的 `<p>`**：三方接线示意图下方的说明行「三台设备共地。S3 的两个 TX 引脚都不接入现有总线；…」是卡片容器 block 的直接子节点，而卡片分支只看 `DIV` 子节点，导致该行丢失。现按卡片前/卡片后分别输出这类直接子 `P`/`UL`/`OL`。
3. **正文内联公式没有围栏**：`k=83.5/296` 这个缩放系数原本是内联 KaTeX，旧还原器直接塞进 TeX 裸文本，读者无法判断它是公式还是笔误。现统一输出为 `$…$`。

### B.3 校验结果

`verify_report.py` 做两件事：把采集件期望正文与报告逐行 diff；再把页面原文 `rawText` 的每一非空行（去 Markdown 语法后）回查报告。

| 项 | 数值 |
| --- | --- |
| 页面原文非空行 | 1107 |
| 报告中缺失行 | 0 |
| 按公式 TeX 核对的行 | 22 行（页面把 2 条公式按字形拆成 17 + 6 行原文：display 公式 `T_{\text{bit}}=\frac{1}{115200}\approx 8.68\ \mu s` 与正文内联公式 `k=83.5/296`） |
| 结构统计 | 正文 29.4 KB；标题 1 个 H1 + 11 个 H2 + 37 个 H3 + 2 个 H4；表格 14 张；代码块 21 个；内联角标 6 个 |
| 逐行 diff | 仅标题下沉与围栏标记差异，属正常（正文下沉 1 级以嵌入交付文档章节） |

公式那 22 行的处理不是放宽校验：采集件里每个公式块都登记了 `tex / delim / frags`，校验器要求**该块的 TeX 源码必须带围栏原样出现在报告中**（`$$…$$` 或 `$…$`），出现才算这些字形行有归宿；先要求片段序列在页面原文里连续成段，接不上才退回逐片匹配，并在输出里单独计数，不与「缺失 0 行」混为一谈。校验器的 `canon()` 同时把 `$` 视为排版记号（与已有的 `*`、反引号、`|`、`#` 同类）剥掉，因此正文里 `$k=83.5/296$` 与页面原文 `k=83.5/296，而不是…` 能对上。

回归：同一套改动后重跑第一轮采集件的校验，`1008 行 / 缺失 0 行`，仍全部通过。

**可复现性**：页面随后落入「无法加载此回复」状态；用 `bsk tab create --url <会话URL>` 在新标签页重新渲染后再次采集，`markdown` / `rawText` / `cites` / `math` 与首次采集**逐字节相同**，说明提取不依赖偶然的渲染时序。

### B.4 已知限制

- 内联角标不带 `href`，URL 只能靠真实点击取得；取得数量受 Chrome 弹窗拦截限制（详见附录 A.1 的逐条标注）。**未取到的归属一律标注为未核实，不按标题臆造 URL。**
- 单选组与交互按钮：本轮回复正文中没有 `role="radiogroup"`，也没有提交按钮，因此没有第二轮自动追问可执行；正文提到的后续选择都写在文字里。
- 代码块语言标签按页面原样保留；页面在代码组件上方显示的中文标签（如「纯文本」）单独记录为 `> 页面代码块标签：…` 一行，不混进代码正文。
- 正文中若出现「本轮内联角标引用」小节以外的引用信息，以附录 A 为准。

---

本文档由 Qoder 通过 browser-skill（`bsk`）驱动用户已登录的浏览器采集生成；同一会话的上一轮采集件见 `C5-S3-双芯片通讯方案-ChatGPT采集.md`。
