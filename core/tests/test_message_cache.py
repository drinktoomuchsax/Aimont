"""Unit tests for the /ingest dedup cache."""

from __future__ import annotations

import asyncio


from aimont.message_cache import MessageIdCache


async def test_add_new_returns_true():
    cache = MessageIdCache()
    assert await cache.add("abc") is True


async def test_add_duplicate_returns_false():
    cache = MessageIdCache()
    await cache.add("abc")
    assert await cache.add("abc") is False


async def test_contains_reflects_presence():
    cache = MessageIdCache()
    await cache.add("x")
    assert await cache.contains("x") is True
    assert await cache.contains("y") is False


async def test_max_size_evicts_oldest():
    cache = MessageIdCache(max_size=3)
    for i in range(5):
        await cache.add(f"id-{i}")
    # Only id-2, id-3, id-4 should remain (LRU eviction).
    assert await cache.contains("id-0") is False
    assert await cache.contains("id-1") is False
    assert await cache.contains("id-2") is True
    assert await cache.contains("id-3") is True
    assert await cache.contains("id-4") is True


async def test_nonpositive_max_size_still_dedups():
    """A misconfigured max_size <= 0 (reachable via an unvalidated
    IngestConfig.dedup_max_size) must not silently disable dedup. Without
    clamping, add() inserts then immediately pops the just-added entry, so
    the cache stays empty and every frame reads as new — the exact
    re-delivery storm this cache prevents."""
    for bad in (0, -5):
        cache = MessageIdCache(max_size=bad)
        assert await cache.add("dup") is True
        assert await cache.add("dup") is False, f"dedup broke with max_size={bad}"
        assert await cache.size() == 1


async def test_nonpositive_ttl_still_dedups():
    """A ttl_sec <= 0 would push the expiry cutoff to now-or-later, expiring
    every entry on the next sweep and disabling dedup. Clamp guards it."""
    for bad in (0.0, -1.0):
        cache = MessageIdCache(ttl_sec=bad)
        assert await cache.add("dup") is True
        assert await cache.add("dup") is False, f"dedup broke with ttl_sec={bad}"


async def test_ttl_expires_entries(monkeypatch):
    cache = MessageIdCache(ttl_sec=0.01)
    await cache.add("old")
    assert await cache.contains("old")
    await asyncio.sleep(0.02)
    assert await cache.contains("old") is False
    # Expired entry can be added again as "new".
    assert await cache.add("old") is True


async def test_refresh_on_duplicate_hit():
    """A duplicate hit should move the id to the MRU position so it
    survives subsequent evictions longer than entries added before it."""
    cache = MessageIdCache(max_size=3)
    await cache.add("a")
    await cache.add("b")
    await cache.add("c")
    # Refresh 'a' — now 'a' is MRU.
    await cache.add("a")
    # Push 'b' out by adding 'd'.
    await cache.add("d")
    assert await cache.contains("a") is True  # refreshed, safe
    assert await cache.contains("b") is False  # evicted
    assert await cache.contains("c") is True
    assert await cache.contains("d") is True


async def test_size_reports_live_count():
    cache = MessageIdCache(ttl_sec=10.0)
    await cache.add("a")
    await cache.add("b")
    assert await cache.size() == 2
    await cache.add("a")  # duplicate
    assert await cache.size() == 2


async def test_duplicate_hit_refreshes_timestamp_not_just_lru_position():
    """Regression for CodeRabbit PR #5 finding #2.

    The bug: if a duplicate hit only moves the entry to the LRU tail
    without refreshing its timestamp, the eviction sweep short-circuits
    on the head and leaves expired entries hiding behind younger ones.

    Setup:
        t=0.00   add A
        t=0.03   add B                   → B's timestamp = 0.03
        t=0.03   add A (duplicate)       → under fix, A's timestamp = 0.03
                                         → under bug, A's timestamp stays 0.00
        t=0.12   contains(A)?
                 TTL=0.05 → cutoff=0.07.
                 Under the fix: A's refreshed timestamp 0.03 < 0.07 → also expired,
                   so we can't use this to distinguish. Different tack:

    Use an age-sensitive assertion: add A, let it age past TTL, then
    refresh it via a duplicate hit, and confirm contains(A) is still True.
    That can only succeed if the timestamp was refreshed.
    """
    cache = MessageIdCache(ttl_sec=0.05)
    await cache.add("A")
    # Let A's original timestamp drift close to — but not past — TTL.
    await asyncio.sleep(0.03)
    # Duplicate hit: must refresh timestamp.
    assert await cache.add("A") is False
    # Sleep beyond the ORIGINAL TTL window but within the refreshed one.
    # With ttl=0.05 and the refresh happening at t≈0.03, the refreshed
    # entry survives until t≈0.08. We check at t≈0.07 — past the
    # unrefreshed deadline (t=0.05), still within the refreshed one.
    await asyncio.sleep(0.04)
    assert await cache.contains("A") is True, "duplicate hit did not refresh TTL"
