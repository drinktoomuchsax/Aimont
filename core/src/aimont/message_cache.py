"""TTL + max-size LRU cache for frame message_id dedup.

Used by /ingest to reject replayed/duplicate frames that arrive via a
different path through the cascade topology. Split-horizon on
`forwarded_by` catches most loops; this cache is the second line of
defense against fan-in re-delivery and honest duplicates from lossy
reconnects.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict


class MessageIdCache:
    """Thread-safe (asyncio) LRU with a TTL per entry.

    - add(id) returns False when the id is already present (dedup hit);
      True when it was freshly recorded.
    - Entries expire after `ttl_sec`.
    - When size exceeds `max_size`, the oldest entry is evicted.
    """

    def __init__(self, ttl_sec: float = 600.0, max_size: int = 1000):
        # Guard both bounds against non-positive values. A max_size <= 0 would
        # pop every entry the instant it's added (len 1 > 0), leaving the cache
        # forever empty; a ttl_sec <= 0 pushes the expiry cutoff to now-or-later
        # so every entry is expired on the next sweep. Either silently disables
        # dedup — add() always returns True — turning the cascade back into the
        # re-delivery storm this cache exists to stop. These values come from
        # IngestConfig ints/floats that aren't otherwise range-checked. Small
        # positive TTLs are legitimate, so only a non-positive one falls back to
        # the default.
        self._ttl = ttl_sec if ttl_sec > 0 else 600.0
        self._max = max(1, max_size)
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    async def add(self, message_id: str) -> bool:
        async with self._lock:
            self._evict_expired_locked()
            now = time.monotonic()
            if message_id in self._entries:
                # Already seen — refresh both LRU position AND timestamp.
                # Refreshing the timestamp is load-bearing: the expiry sweep
                # below short-circuits once it hits the first non-expired
                # entry, so any stale-timestamp entry that gets bumped to
                # the tail would hide newer (younger) entries in front of
                # it and keep expired ids cached past their TTL.
                self._entries[message_id] = now
                self._entries.move_to_end(message_id)
                return False
            self._entries[message_id] = now
            if len(self._entries) > self._max:
                self._entries.popitem(last=False)
            return True

    async def contains(self, message_id: str) -> bool:
        async with self._lock:
            self._evict_expired_locked()
            return message_id in self._entries

    async def size(self) -> int:
        async with self._lock:
            self._evict_expired_locked()
            return len(self._entries)

    def _evict_expired_locked(self) -> None:
        cutoff = time.monotonic() - self._ttl
        # Short-circuit relies on the head being the oldest-by-timestamp.
        # We maintain that invariant by refreshing both the timestamp AND
        # the LRU position together in add() — otherwise an expired entry
        # at the head would mask newer entries past the break.
        while self._entries:
            oldest_id = next(iter(self._entries))
            if self._entries[oldest_id] >= cutoff:
                break
            del self._entries[oldest_id]
