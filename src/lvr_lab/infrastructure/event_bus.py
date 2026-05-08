"""
In-process event bus — publishes domain events to subscribed handlers.

Production deployment swaps this for Redis Streams or NATS or Kafka. The
interface here matches what those backends expose so the swap is mechanical.
"""

from __future__ import annotations
from collections import defaultdict
from typing import Callable, Type

from ..domain.events import DomainEvent


EventHandler = Callable[[DomainEvent], None]


class EventBus:
    """Synchronous in-process event bus.

    Usage:
        bus = EventBus()
        bus.subscribe(SwapEvent, my_handler)
        bus.publish(swap_event)

    For production, swap with:
        - RedisStreamsBus(url=...)
        - NatsBus(servers=...)
    Both implement the same interface.
    """

    def __init__(self):
        self._subscribers: dict[Type[DomainEvent], list[EventHandler]] = defaultdict(list)
        self._stats = {"published": 0, "delivered": 0, "errors": 0}

    def subscribe(self, event_type: Type[DomainEvent], handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: Type[DomainEvent], handler: EventHandler) -> None:
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    def publish(self, event: DomainEvent) -> int:
        """Deliver to all subscribers; return count delivered. Errors logged but don't propagate."""
        self._stats["published"] += 1
        delivered = 0
        for handler in self._subscribers.get(type(event), []):
            try:
                handler(event)
                delivered += 1
                self._stats["delivered"] += 1
            except Exception as e:
                self._stats["errors"] += 1
                # In production: log to Sentry. For now, print but don't crash.
                import sys
                print(f"[event_bus] handler error on {type(event).__name__}: {e}", file=sys.stderr)
        return delivered

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)
