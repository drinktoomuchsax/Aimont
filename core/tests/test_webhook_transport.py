"""Tests for the webhook transport."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aimont.models import AggregateFrame, HostIdentity, PresenceFrame, AimontState, StateFrame
from aimont.transports import get_transport_class
from aimont.transports.webhook import WebhookTransport


def _state_frame() -> StateFrame:
    return StateFrame(
        session_id="s1",
        state=AimontState.WORKING,
        previous=AimontState.IDLE,
        timestamp=datetime.now(timezone.utc),
    )


def test_registered_under_webhook_name():
    assert get_transport_class("webhook") is WebhookTransport


async def test_inert_without_url_does_not_raise():
    t = WebhookTransport("webhook", {})
    await t.start()
    await t.send(_state_frame())  # no client → no-op
    await t.stop()


class _FakeClient:
    def __init__(self):
        self.posts: list[tuple[str, str]] = []
        self.closed = False

    async def post(self, url, content=None):
        self.posts.append((url, content))

    async def aclose(self):
        self.closed = True


async def test_send_posts_frame_json(monkeypatch):
    fake = _FakeClient()
    t = WebhookTransport("webhook", {"url": "http://example.test/hook"})
    # Replace the client that start() would build.
    await t.start()
    t._client = fake

    frame = _state_frame()
    await t.send(frame)
    # Delivery is a background task; drain it.
    await asyncio.gather(*t._pending, return_exceptions=True)

    assert len(fake.posts) == 1
    url, body = fake.posts[0]
    assert url == "http://example.test/hook"
    assert '"session_id":"s1"' in body.replace(" ", "")

    await t.stop()
    assert fake.closed


async def test_aggregate_frame_is_posted():
    fake = _FakeClient()
    t = WebhookTransport("webhook", {"url": "http://example.test/hook"})
    await t.start()
    t._client = fake

    agg = AggregateFrame(
        state=AimontState.IDLE,
        active_sessions=0,
        breakdown={},
        timestamp=datetime.now(timezone.utc),
    )
    await t.send_aggregate(agg)
    await asyncio.gather(*t._pending, return_exceptions=True)
    assert len(fake.posts) == 1
    assert '"type":"aggregate"' in fake.posts[0][1].replace(" ", "")
    await t.stop()


async def test_presence_frame_is_posted():
    fake = _FakeClient()
    t = WebhookTransport("webhook", {"url": "http://example.test/hook"})
    await t.start()
    t._client = fake

    pres = PresenceFrame(
        host=HostIdentity(host_id="h1"),
        status="online",
        timestamp=datetime.now(timezone.utc),
    )
    await t.send(pres)
    await asyncio.gather(*t._pending, return_exceptions=True)
    assert '"type":"presence"' in fake.posts[0][1].replace(" ", "")
    await t.stop()


async def test_failing_post_is_swallowed():
    class BoomClient(_FakeClient):
        async def post(self, url, content=None):
            raise RuntimeError("network down")

    t = WebhookTransport("webhook", {"url": "http://example.test/hook"})
    await t.start()
    t._client = BoomClient()
    await t.send(_state_frame())
    # Must not raise even though the POST fails.
    await asyncio.gather(*t._pending, return_exceptions=True)
    await t.stop()


async def test_invalid_url_scheme_disables_transport(caplog):
    import logging

    t = WebhookTransport("webhook", {"url": "example.com/hook"})  # no scheme
    with caplog.at_level(logging.WARNING):
        await t.start()
    assert t._client is None  # stayed inert
    assert t._url is None
    assert any("invalid url" in r.message for r in caplog.records)
    # send is a safe no-op when disabled
    await t.send(_state_frame())
    await t.stop()


async def test_pending_set_is_bounded_and_sheds_load():
    """A slow endpoint must not let the in-flight POST set grow without bound;
    once max_pending is reached, new frames are dropped."""
    gate = asyncio.Event()

    class SlowClient(_FakeClient):
        async def post(self, url, content=None):
            await gate.wait()  # hang until released
            await super().post(url, content)

    t = WebhookTransport("webhook", {"url": "http://example.test/hook", "max_pending": 3})
    await t.start()
    t._client = SlowClient()

    # Fire more frames than the cap while all POSTs are hung.
    for _ in range(10):
        await t.send(_state_frame())

    assert len(t._pending) == 3
    assert t._dropped == 7

    # Release the hung POSTs and let them drain.
    gate.set()
    await asyncio.gather(*t._pending, return_exceptions=True)
    await t.stop()


async def test_stop_rejects_posts_issued_mid_drain():
    """A frame broadcast while stop() is draining in-flight POSTs (client still
    open) must not spawn a task that escapes the drain and races aclose()."""
    drain_started = asyncio.Event()
    release = asyncio.Event()

    class SlowClient(_FakeClient):
        async def post(self, url, content=None):
            drain_started.set()
            await release.wait()
            await super().post(url, content)

    t = WebhookTransport("webhook", {"url": "http://example.test/hook"})
    await t.start()
    t._client = SlowClient()

    await t.send(_state_frame())  # one in-flight POST, now hung
    stop_task = asyncio.create_task(t.stop())
    await drain_started.wait()  # stop() is now awaiting the gather

    # Broadcast arrives mid-shutdown: client is still open (aclose not reached).
    assert t._client is not None
    await t.send(_state_frame())
    assert len(t._pending) == 1  # rejected — did NOT spawn a second task

    release.set()
    await stop_task
    assert t._client is None


async def test_valid_https_url_builds_client():
    t = WebhookTransport("webhook", {"url": "https://hooks.example.com/aimont"})
    await t.start()
    assert t._client is not None
    await t.stop()
