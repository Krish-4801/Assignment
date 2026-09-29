"""
Domain models and enums for the order fulfillment simulation.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
import time


class ItemType(Enum):
    COLD = "COLD"
    SHELF = "SHELF"


class ItemStatus(Enum):
    IN_STORAGE = "IN_STORAGE"
    TRANSIT = "TRANSIT"
    BUFFERED = "BUFFERED"
    PACKING = "PACKING"
    PACKED = "PACKED"
    SPOILED = "SPOILED"


class OrderStatus(Enum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    BUFFER_STAGING = "BUFFER_STAGING"
    READY_TO_PACK = "READY_TO_PACK"
    PACKING = "PACKING"
    COMPLETED = "COMPLETED"
    FAILED_SPOILED = "FAILED_SPOILED"


def format_time_minutes(minutes: float) -> str:
    """Formats a duration in minutes into a MM:SS string (e.g. 03:45)."""
    if minutes == float("inf") or minutes >= 999:
        return "∞"
    total_seconds = int(round(minutes * 60))
    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    mins = total_seconds // 60
    secs = total_seconds % 60
    return f"{sign}{mins:02d}:{secs:02d}"


@dataclass
class Item:
    item_id: str
    order_id: str
    item_type: ItemType
    # Freshness / melting metrics (measured in MINUTES)
    max_ttl: float  # Total lifespan outside cold storage (3.0 - 4.0 minutes)
    remaining_ttl: float
    status: ItemStatus = ItemStatus.IN_STORAGE
    transit_duration: float = 1.0  # Randomized travel time in minutes from storage to buffer
    transit_progress: float = 0.0  # Elapsed transit time in minutes
    wait_at_buffer_time: float = 0.0  # Time spent waiting outside buffer if buffer is full (minutes)
    chilled_buffer_protection: float = 0.33  # Decay rate multiplier when in chilled buffer table (3x slower)

    @property
    def freshness_percentage(self) -> float:
        if self.item_type == ItemType.SHELF:
            return 100.0
        if self.max_ttl <= 0:
            return 0.0
        return max(0.0, min(100.0, (self.remaining_ttl / self.max_ttl) * 100.0))

    @property
    def is_spoiled(self) -> bool:
        return self.item_type == ItemType.COLD and self.remaining_ttl <= 0.0

    def tick_decay(self, dt: float, in_chilled_buffer: bool = False):
        """Applies degradation if outside cold storage (dt in minutes)."""
        if self.item_type != ItemType.COLD or self.status in (ItemStatus.IN_STORAGE, ItemStatus.PACKED, ItemStatus.SPOILED):
            return

        decay_rate = self.chilled_buffer_protection if (in_chilled_buffer and self.status == ItemStatus.BUFFERED) else 1.0
        self.remaining_ttl -= dt * decay_rate
        if self.remaining_ttl <= 0.0:
            self.remaining_ttl = 0.0
            self.status = ItemStatus.SPOILED


@dataclass
class Order:
    order_id: str
    items: List[Item]
    created_at: float  # In minutes
    status: OrderStatus = OrderStatus.PENDING
    cold_dispatched: bool = False
    shelf_dispatched: bool = False
    started_packing_at: Optional[float] = None
    completed_at: Optional[float] = None
    pack_duration: float = 0.8  # Minutes required at the CPU packaging station
    pack_progress: float = 0.0

    @property
    def cold_items(self) -> List[Item]:
        return [item for item in self.items if item.item_type == ItemType.COLD]

    @property
    def shelf_items(self) -> List[Item]:
        return [item for item in self.items if item.item_type == ItemType.SHELF]

    @property
    def total_items(self) -> int:
        return len(self.items)

    @property
    def cold_percentage(self) -> float:
        if not self.items:
            return 0.0
        return (len(self.cold_items) / len(self.items)) * 100.0

    @property
    def min_cold_freshness(self) -> float:
        colds = self.cold_items
        if not colds:
            return 100.0
        return min(item.freshness_percentage for item in colds)

    @property
    def min_cold_remaining_ttl(self) -> float:
        colds = self.cold_items
        if not colds:
            return float("inf")
        return min(item.remaining_ttl for item in colds)

    @property
    def is_any_cold_spoiled(self) -> bool:
        return any(item.is_spoiled for item in self.cold_items)

    @property
    def are_all_items_in_buffer(self) -> bool:
        return all(item.status == ItemStatus.BUFFERED for item in self.items)

    @property
    def is_fully_assembled(self) -> bool:
        """True if all items have arrived at buffer table or packaging station."""
        return all(item.status in (ItemStatus.BUFFERED, ItemStatus.PACKING, ItemStatus.PACKED) for item in self.items)

    @property
    def slack_time(self) -> float:
        """
        Slack time = (Remaining TTL of most fragile cold item) - (Packing time left)
        Used for CPU-style Least Slack Time (LST) / Earliest Deadline First (EDF) scheduling.
        """
        if not self.cold_items:
            return 9999.0  # Shelf-only has virtually infinite slack
        rem_pack = max(0.0, self.pack_duration - self.pack_progress)
        return self.min_cold_remaining_ttl - rem_pack
