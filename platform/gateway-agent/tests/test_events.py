"""EventBus pub/sub + history。"""

import threading

from widscan_agent.events import Event, EventBus


def test_basic_publish_subscribe():
    bus = EventBus()
    seen = []
    bus.subscribe("test.event", lambda e: seen.append(e))
    bus.publish(Event(type="test.event", data={"v": 42}))
    bus.publish(Event(type="other", data={"v": 1}))
    assert len(seen) == 1
    assert seen[0].data["v"] == 42


def test_wildcard_subscription():
    bus = EventBus()
    seen = []
    bus.subscribe("*", lambda e: seen.append(e.type))
    bus.publish(Event(type="a", data={}))
    bus.publish(Event(type="b", data={}))
    bus.publish(Event(type="c", data={}))
    assert seen == ["a", "b", "c"]


def test_history():
    bus = EventBus()
    for i in range(10):
        bus.publish(Event(type="evt", data={"i": i}))
    recent = bus.recent()
    assert len(recent) == 10
    filtered = bus.recent(event_type="evt", limit=3)
    assert len(filtered) == 3
    assert filtered[-1].data["i"] == 9


def test_handler_exception_is_caught():
    bus = EventBus()
    bad = lambda e: (_ for _ in ()).throw(ValueError("boom"))
    good_seen = []
    bus.subscribe("evt", bad)
    bus.subscribe("evt", lambda e: good_seen.append(1))
    # 不应崩
    bus.publish(Event(type="evt", data={}))
    assert len(good_seen) == 1


def test_multithreaded_publish():
    bus = EventBus()
    lock = threading.Lock()
    counts = [0]

    def handler(e):
        with lock:
            counts[0] += 1

    bus.subscribe("*", handler)
    threads = [threading.Thread(target=lambda: [bus.publish(Event(type="x", data={})) for _ in range(100)]) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counts[0] == 500
