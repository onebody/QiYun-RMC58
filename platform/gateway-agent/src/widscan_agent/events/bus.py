"""事件总线 — 进程内 pub/sub。

**故意不用 asyncio.Queue** — Bus 本身就是同步的；消费者自己决定
怎么 await（FastAPI 的 WebSocket endpoint 会把它们包成 async）。

这样 Bus 在测试里也能直接用，不用跑 event loop。
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Event:
    """Agent 内任何事件的统一壳。"""
    type: str                        # 例如 "device.online", "device.frame", "agent.started"
    data: Dict[str, Any] = field(default_factory=dict)
    device_id: Optional[str] = None  # 若事件来自某个设备，填上它
    ts: float = 0.0                  # monotonic time

    def to_jsonable(self) -> Dict[str, Any]:
        import time as _time
        return {
            "type": self.type,
            "device_id": self.device_id,
            "ts": _time.time(),
            "data": self.data,
        }


EventHandler = Callable[[Event], None]


class EventBus:
    """线程安全 pub/sub。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: Dict[str, List[EventHandler]] = defaultdict(list)
        self._wildcard: List[EventHandler] = []
        self._history: List[Event] = []
        self._history_limit: int = 500

    # --- subscribe -----------------------------------------------------

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            if event_type == "*":
                self._wildcard.append(handler)
            else:
                self._subs[event_type].append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        with self._lock:
            for hlist in self._subs.values():
                if handler in hlist:
                    hlist.remove(handler)
            if handler in self._wildcard:
                self._wildcard.remove(handler)

    # --- publish -------------------------------------------------------

    def publish(self, event: Event) -> None:
        """同步分发给所有 handler。handler 里抛异常会被捕获并记日志。"""
        import time as _time
        if event.ts == 0.0:
            event.ts = _time.monotonic()
        self._append_history(event)
        # 先复制列表避免订阅中修改
        with self._lock:
            handlers = list(self._subs.get(event.type, [])) + list(self._wildcard)
        for h in handlers:
            try:
                h(event)
            except Exception as exc:  # noqa: BLE001 — pub/sub 不应该因为坏 handler 崩
                import logging
                logging.getLogger(__name__).warning("event handler error: %s", exc)

    # --- history -------------------------------------------------------

    def _append_history(self, event: Event) -> None:
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit :]

    def recent(self, event_type: Optional[str] = None, limit: int = 50) -> List[Event]:
        with self._lock:
            h = list(self._history)
        if event_type:
            h = [e for e in h if e.type == event_type]
        return h[-limit:]
