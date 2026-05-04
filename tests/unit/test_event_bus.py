from datetime import datetime

from code_minions.engine.event_bus import Event, EventBus


def test_pubsub_basic():
    got = []
    bus = EventBus()
    bus.subscribe(lambda e: got.append(e.kind))
    bus.publish(Event(run_id="r", kind="x", payload={}, ts=datetime.now()))
    assert got == ["x"]
