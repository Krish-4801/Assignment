"""
Buffer Table implementation acting as a Reorder Buffer (ROB) / Cache
between Storage facilities and the Packaging Station CPU.
"""
from typing import Dict, List, Optional
from models import Item, ItemType, ItemStatus, Order, OrderStatus


class BufferTable:
    def __init__(self, capacity: int = 12, chilled_protection: bool = True):
        self.capacity = capacity
        self.chilled_protection = chilled_protection
        # Maps order_id -> list of buffered items
        self.staging: Dict[str, List[Item]] = {}

    @property
    def total_buffered_items(self) -> int:
        return sum(len(items) for items in self.staging.values())

    @property
    def available_slots(self) -> int:
        return max(0, self.capacity - self.total_buffered_items)

    @property
    def is_full(self) -> bool:
        return self.total_buffered_items >= self.capacity

    @property
    def utilization_percentage(self) -> float:
        if self.capacity <= 0:
            return 100.0
        return (self.total_buffered_items / self.capacity) * 100.0

    def can_accept(self, item_count: int = 1) -> bool:
        return self.available_slots >= item_count

    def accept_item(self, item: Item) -> bool:
        """Stores an arriving item into the buffer table."""
        if self.is_full:
            return False

        item.status = ItemStatus.BUFFERED
        if item.order_id not in self.staging:
            self.staging[item.order_id] = []
        self.staging[item.order_id].append(item)
        return True

    def is_order_complete(self, order: Order) -> bool:
        """Checks if all items of this order have reached the buffer."""
        buffered_items = self.staging.get(order.order_id, [])
        return len(buffered_items) == order.total_items

    def release_order_items(self, order: Order) -> List[Item]:
        """Atomically releases all buffered items of the order to the Packaging CPU."""
        items = self.staging.pop(order.order_id, [])
        for item in items:
            item.status = ItemStatus.PACKING
        return items

    def discard_spoiled_order(self, order: Order) -> List[Item]:
        """Cleans up items if order failed due to melting in the buffer."""
        items = self.staging.pop(order.order_id, [])
        for item in items:
            if item.item_type == ItemType.COLD:
                item.status = ItemStatus.SPOILED
            else:
                item.status = ItemStatus.IN_STORAGE  # Restocked
        return items

    def tick(self, dt: float):
        """Simulates time tick in buffer table, applying chilled protection if enabled."""
        for order_id, items in self.staging.items():
            for item in items:
                item.tick_decay(dt, in_chilled_buffer=self.chilled_protection)
