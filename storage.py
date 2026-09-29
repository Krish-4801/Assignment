"""
Storage facilities and transit pipeline.
Handles dispatching items from Cold and Shelf storage to the buffer table.
Supports both Naive (simultaneous) and Robust (JIT Staggered) dispatching.
"""
from typing import List, Callable, Optional
from models import Item, ItemType, ItemStatus, Order, OrderStatus


class StoragePipeline:
    def __init__(self):
        # Items currently traveling on conveyors/runners to the buffer table
        self.in_transit: List[Item] = []

    def dispatch_item(self, item: Item):
        item.status = ItemStatus.TRANSIT
        item.transit_progress = 0.0
        self.in_transit.append(item)

    def tick(
        self,
        dt: float,
        on_item_arrived: Callable[[Item], bool],
        on_item_spoiled: Optional[Callable[[Item], None]] = None,
        on_deadlock: Optional[Callable[[Item], None]] = None,
    ):
        """
        Advances items along the transit pipeline.
        If an item reaches transit_duration, it arrives at the buffer table.
        Cold items decay while in transit (ambient room temperature conveyor).
        """
        still_transit = []
        for item in self.in_transit:
            # Transit decay
            item.tick_decay(dt, in_chilled_buffer=False)

            if item.is_spoiled:
                # Spoiled during transit conveyor
                if on_item_spoiled:
                    on_item_spoiled(item)
                continue

            item.transit_progress += dt
            if item.transit_progress >= item.transit_duration:
                accepted = on_item_arrived(item)
                if not accepted:
                    # Buffer full - hold on transit conveyor (still decays!)
                    item.wait_at_buffer_time += dt
                    # If stuck waiting for buffer space for too long (3 minutes), trigger jam/deadlock
                    if item.wait_at_buffer_time >= 3.0:
                        if on_deadlock:
                            on_deadlock(item)
                        continue
                    still_transit.append(item)
                else:
                    item.wait_at_buffer_time = 0.0
            else:
                still_transit.append(item)

        self.in_transit = still_transit


class Dispatcher:
    """
    Orchestrates order dispatching from storage.
    Can toggle features independently:
      - enable_jit: Stagger dispatch so cold items only leave when shelf items are near buffer.
      - enable_admission: Throttle active in-flight orders to avoid overwhelming buffer and CPU.
    """
    def __init__(
        self,
        enable_jit: bool = True,
        enable_admission: bool = True,
        transit_duration: float = 1.0,  # Expected transit time in minutes
        max_in_flight: int = 4,
    ):
        self.enable_jit = enable_jit
        self.enable_admission = enable_admission
        self.transit_duration = transit_duration
        self.max_in_flight = max_in_flight

    def evaluate_order(
        self,
        order: Order,
        buffer_is_congested: bool,
        available_buffer_slots: int,
        active_in_flight_count: int,
        storage_pipeline: StoragePipeline,
    ) -> List[str]:
        events = []

        if order.status in (OrderStatus.COMPLETED, OrderStatus.FAILED_SPOILED, OrderStatus.PACKING):
            return events

        # 1. Admission Control Check
        has_started = (order.shelf_dispatched or order.cold_dispatched)
        if self.enable_admission and not has_started:
            if (
                active_in_flight_count >= self.max_in_flight
                or buffer_is_congested
                or available_buffer_slots < order.total_items
            ):
                return events  # Kept in storage safely

        # 2. Dispatch Logic (JIT vs Immediate Simultaneous)
        if not self.enable_jit:
            # Immediate dispatch of all items
            if not order.shelf_dispatched:
                for item in order.shelf_items:
                    storage_pipeline.dispatch_item(item)
                order.shelf_dispatched = True
                if order.shelf_items:
                    events.append(f"📦 [Order {order.order_id}] Dispatched {len(order.shelf_items)} shelf items (Immediate)")

            if not order.cold_dispatched:
                for item in order.cold_items:
                    storage_pipeline.dispatch_item(item)
                order.cold_dispatched = True
                if order.cold_items:
                    events.append(f"❄️ [Order {order.order_id}] Dispatched {len(order.cold_items)} cold items (Immediate)")

            order.status = OrderStatus.DISPATCHING

        else:
            # JIT Staggered Picking: Shelf items first, cold items delayed
            if not order.shelf_dispatched:
                for item in order.shelf_items:
                    storage_pipeline.dispatch_item(item)
                order.shelf_dispatched = True
                order.status = OrderStatus.DISPATCHING
                if order.shelf_items:
                    events.append(f"📦 [Order {order.order_id}] JIT Phase 1: Dispatched {len(order.shelf_items)} shelf items first")

            if not order.cold_dispatched:
                shelf_ready = False
                if not order.shelf_items:
                    shelf_ready = True
                else:
                    shelf_progresses = [
                        (item.transit_progress / item.transit_duration if item.status == ItemStatus.TRANSIT else 1.0)
                        for item in order.shelf_items
                    ]
                    min_progress = min(shelf_progresses) if shelf_progresses else 1.0
                    if min_progress >= 0.70:
                        shelf_ready = True

                if shelf_ready:
                    for item in order.cold_items:
                        storage_pipeline.dispatch_item(item)
                    order.cold_dispatched = True
                    if order.cold_items:
                        events.append(f"❄️ [Order {order.order_id}] JIT Phase 2: Shelf ready -> Dispatched {len(order.cold_items)} cold items")

        return events
