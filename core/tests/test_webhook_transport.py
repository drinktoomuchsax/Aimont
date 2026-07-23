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
