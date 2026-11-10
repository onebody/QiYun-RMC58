"""protocol 包 — 封装帧解码、握手和命令白名单。

**核心规则**：Gateway Agent **不自己实现**帧解析，它 import
`rftrack.backend.app.services.frame_parser` 里的 `FrameStream` 和
`parse_info_line`。本节只做三件事：

1. 优雅地定位 frame_parser（monorepo 或 `WIDSCAN_FRAME_PARSER_PATH`）
2. 包装成 "读 → 喂 FrameStream → 发事件" 的循环
3. 命令白名单校验 + 超时回复
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


def _resolve_frame_parser_module():
    """按优先级找 frame_parser.py 并 import 进来。"""
    # 1. 已经在 sys.path 里（作为 monorepo 子包被 pip install -e）
    try:
        import rftrack.backend.app.services.frame_parser as mod  # noqa: F401
        return mod
    except ImportError:
        pass

    # 2. 项目根/repo/rftrack/backend/app/services 相对路径
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "repo" / "rftrack" / "backend" / "app" / "services" / "frame_parser.py"
        if candidate.is_file():
            sys.path.insert(0, str(candidate.parent))
            import frame_parser as mod  # type: ignore[import]
            return mod

    # 3. WIDSCAN_FRAME_PARSER_PATH 环境变量指向目录
    env_path = os.environ.get("WIDSCAN_FRAME_PARSER_PATH")
    if env_path:
        p = Path(env_path)
        if (p / "frame_parser.py").is_file():
            sys.path.insert(0, str(p))
            import frame_parser as mod  # type: ignore[import]
            return mod

    raise ImportError(
        "frame_parser not found. Install rftrack backend as editable, "
        "or set WIDSCAN_FRAME_PARSER_PATH to its directory."
    )


# 在模块 import 时解析一次，失败会 raise — 启动时就知道缺了什么。
_frame_parser = _resolve_frame_parser_module()

# 暴露到本模块顶层供使用
FrameStream = _frame_parser.FrameStream  # type: ignore[attr-defined]
StreamItem = _frame_parser.StreamItem    # type: ignore[attr-defined]
parse_info_line = _frame_parser.parse_info_line  # type: ignore[attr-defined]
decode_frame = _frame_parser.decode       # type: ignore[attr-defined]


# --- 命令白名单 -----------------------------------------------------------
# 固件允许在 monitor 和 RH 两种模式都执行的命令子集。Agent 只把这些命令透传。
# 完整列表见 firmware/src/proto/commands.c 的 rh_allowed() + monitor 专属命令。

# "原样透传" — 单行命令
ALLOWED_SINGLE: set[str] = {
    "INFO", "PROTOCOL?", "PROTOCOL MONITOR", "PROTOCOL RH",
    "STATUS?", "CONFIG?", "CONFIG SAVE", "CONFIG RESET",
    "METRICS?", "GAIN?", "GAIN HARDWARE", "MODE GROUP", "MODE SINGLE",
    "SCAN START", "SCAN STOP", "REBOOT", "BOOTLOADER",
    "ENABLE?", "PROFILE?", "TIMING?", "SPURS?", "SPURS CLEAR",
    "FFTTYPE?", "LEVELFILTER?", "PHYIDLE?", "GPIO?", "GPIO SAVE",
    "CHANNELS DEFAULT",
}

# "前缀匹配" — 命令本身带参数的。前缀**统一不带结尾空格**，
# 这样 "GAIN MANUAL" 和 "GAIN MANUAL 45" 都能匹配（后者是前缀 + 空格 + 参数）。
ALLOWED_PREFIXES: tuple[str, ...] = (
    "GAIN MANUAL",
    "FILTER",
    "FFT",
    "SEGMENTS",
    "CAPN",
    "RATE",
    "SETTLE",
    "IQCORR",
    "SPEC",
    "RAWIQ",
    "WIN",
    "EMA",
    "AVG",
    "THROTTLE",
    "PHYIDLE",
    "TUNESKIP",
    "REGWATCH",
    "SPURS ADD",
    "SPURS GUARD",
    "FFTEVAL SYNTH",
    "FFTEVAL LIVE",
    "FFTTYPE",
    "LEVELFILTER",
    "GPIO",
    "CHANNELS",
    "ENABLE",
    "MODE",
    "METRICS",
)


def is_command_allowed(line: str) -> bool:
    """校验单行命令是否在 Agent 白名单内。不区分大小写 — 固件命令是大写。"""
    cmd = line.strip().rstrip("\r\n")
    if not cmd:
        return False
    if cmd in ALLOWED_SINGLE:
        return True
    for prefix in ALLOWED_PREFIXES:
        if cmd == prefix or cmd.startswith(prefix + " "):
            return True
    return False


def find_frame_parser_module_path() -> str:
    """返回 frame_parser.py 真实路径 — 供诊断端点使用。"""
    try:
        return str(Path(_frame_parser.__file__).resolve())  # type: ignore[attr-defined]
    except Exception:
        return "<unknown>"
