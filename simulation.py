"""
Simulation engine coordinating orders, storage dispatch, buffer table, and CPU packaging station.
"""
import random
from typing import List, Dict, Any, Optional
from models import Item, ItemType, ItemStatus, Order, OrderStatus, format_time_minutes
from buffer_table import BufferTable
from storage import StoragePipeline, Dispatcher
from packaging_station import PackagingStationCPU


class Simulation:
    def __init__(
        self,
        num_orders: int = 15,
        mode: str = "ROBUST",  # 'ROBUST', 'NAIVE', or 'CUSTOM'
        feature_jit: Optional[bool] = None,
        feature_lst: Optional[bool] = None,
        feature_chilled_buffer: Optional[bool] = None,
        feature_rob: Optional[bool] = None,
        feature_admission: Optional[bool] = None,
        num_cores: int = 2,
        buffer_capacity: int = 12,
        seed: Optional[int] = 42,
        custom_orders: Optional[List[Order]] = None,
        rush_hour: bool = False,
        pack_time_multiplier: float = 1.0,
    ):
        self.num_orders = num_orders if custom_orders is None else len(custom_orders)
        self.mode = mode.upper()
        self.rush_hour = rush_hour
        self.pack_time_multiplier = (2.0 if rush_hour and pack_time_multiplier == 1.0 else pack_time_multiplier)
        self.sim_time = 0.0  # Measured in MINUTES
        self.is_paused = False
        self.time_scale = 1.0
        self.event_log: List[str] = []

        if seed is not None:
            random.seed(seed)

        # Resolve feature flags: default to mode if not specified
        is_robust = (self.mode == "ROBUST")
        self.feature_jit = feature_jit if feature_jit is not None else is_robust
        self.feature_lst = feature_lst if feature_lst is not None else is_robust
        self.feature_chilled_buffer = feature_chilled_buffer if feature_chilled_buffer is not None else is_robust
        self.feature_rob = feature_rob if feature_rob is not None else is_robust
        self.feature_admission = feature_admission if feature_admission is not None else is_robust

        # During rush hour, in-flight limit throttles tighter to avoid queue pileup
        in_flight_cap = max(2, num_cores) if self.rush_hour else max(2, num_cores * 2)

        self.dispatcher = Dispatcher(
            enable_jit=self.feature_jit,
            enable_admission=self.feature_admission,
            transit_duration=1.0,  # Average 1 minute transit
            max_in_flight=in_flight_cap,
        )
        self.storage_pipeline = StoragePipeline()
        self.buffer_table = BufferTable(
            capacity=buffer_capacity,
            chilled_protection=self.feature_chilled_buffer,
        )
        self.cpu = PackagingStationCPU(
            num_cores=num_cores,
            scheduling_policy="LST" if self.feature_lst else "FIFO",
        )

        # Orders list: use custom_orders if provided, else generate
        if custom_orders is not None:
            self.orders = custom_orders
            if self.rush_hour:
                for o in self.orders:
                    o.pack_duration *= self.pack_time_multiplier
        else:
            self.orders = []
            self._generate_orders()

        if self.rush_hour:
            self.log(f"⚠️ RUSH HOUR ACTIVE: Packaging time increased by {self.pack_time_multiplier:.1f}x (avg {1.4 * self.pack_time_multiplier / 2:.1f}-{2.0 * self.pack_time_multiplier / 2:.1f} min)")

        # Tracked Metrics
        self.completed_orders: List[Order] = []
        self.spoiled_orders: List[Order] = []
        self.peak_buffer_usage: int = 0

    def _generate_orders(self):
        """Generates N orders with randomized cold/shelf distributions and randomized transit times."""
        for i in range(1, self.num_orders + 1):
            order_id = f"ORD-{i:02d}"
            # Total items in order between 2 and 4
            num_items = random.randint(2, 4)
            # Random cold percentage between 0% and 100%
            cold_prob = random.uniform(0.1, 0.9)

            items = []
            for j in range(1, num_items + 1):
                item_id = f"{order_id}-ITM{j}"
                is_cold = (random.random() < cold_prob)

                if is_cold:
                    # Cold items: Melting window is 3.0 to 4.0 MINUTES outside cold storage
                    ttl = random.uniform(3.0, 4.0)
                    # Randomized transit time from cold storage: 0.4 to 0.9 minutes (24s - 54s)
                    transit = random.uniform(0.4, 0.9)
                    items.append(
                        Item(
                            item_id=item_id,
                            order_id=order_id,
                            item_type=ItemType.COLD,
                            max_ttl=ttl,
                            remaining_ttl=ttl,
                            transit_duration=transit,
                        )
                    )
                else:
                    # Shelf items: Non-perishable, randomized transit from aisles: 0.6 to 1.4 minutes (36s - 84s)
                    transit = random.uniform(0.6, 1.4)
                    items.append(
                        Item(
                            item_id=item_id,
                            order_id=order_id,
                            item_type=ItemType.SHELF,
                            max_ttl=9999.0,
                            remaining_ttl=9999.0,
                            transit_duration=transit,
                        )
                    )

            # Ensure at least 1 cold item for realistic testing in some orders
            if i % 2 == 0 and not any(it.item_type == ItemType.COLD for it in items):
                ttl = random.uniform(3.0, 4.0)
                items[0] = Item(
                    item_id=items[0].item_id,
                    order_id=order_id,
                    item_type=ItemType.COLD,
                    max_ttl=ttl,
                    remaining_ttl=ttl,
                    transit_duration=random.uniform(0.4, 0.9),
                )

            order = Order(
                order_id=order_id,
                items=items,
                created_at=0.0,
                pack_duration=random.uniform(0.5, 1.0) * self.pack_time_multiplier,  # Scaled under rush hour
            )
            self.orders.append(order)

    def log(self, message: str):
        timestamp = f"[{format_time_minutes(self.sim_time)}]"
        self.event_log.append(f"{timestamp} {message}")
        if len(self.event_log) > 100:
            self.event_log.pop(0)

    @property
    def is_finished(self) -> bool:
        return len(self.completed_orders) + len(self.spoiled_orders) == len(self.orders)

    def abort_order(self, order: Order, reason: str):
        """Aborts an order, discards melted items, clears storage pipeline, and frees buffer slots."""
        if order.status in (OrderStatus.COMPLETED, OrderStatus.FAILED_SPOILED):
            return
        order.status = OrderStatus.FAILED_SPOILED
        self.buffer_table.discard_spoiled_order(order)
        # Clear any orphan items of this order from transit conveyor
        self.storage_pipeline.in_transit = [
            it for it in self.storage_pipeline.in_transit if it.order_id != order.order_id
        ]
        if order not in self.spoiled_orders:
            self.spoiled_orders.append(order)
        self.log(f"💥 [Order {order.order_id}] FAILED! {reason}")

    def tick(self, dt: float):
        if self.is_paused or self.is_finished:
            return

        self.sim_time += dt

        # 1. Backpressure & concurrency tracking
        buffer_congested = (self.buffer_table.utilization_percentage >= 70.0)

        # 2. Dispatcher evaluates uncompleted orders with dynamic admission control
        for order in self.orders:
            if not (order.shelf_dispatched and order.cold_dispatched):
                current_in_flight = sum(
                    1 for o in self.orders
                    if o.status in (OrderStatus.DISPATCHING, OrderStatus.BUFFER_STAGING, OrderStatus.READY_TO_PACK, OrderStatus.PACKING)
                )
                events = self.dispatcher.evaluate_order(
                    order,
                    buffer_is_congested=buffer_congested,
                    available_buffer_slots=self.buffer_table.available_slots,
                    active_in_flight_count=current_in_flight,
                    storage_pipeline=self.storage_pipeline,
                )
                for ev in events:
                    self.log(ev)

        # 3. Advance Storage Transit pipeline
        def on_arrived(item: Item) -> bool:
            order = next((o for o in self.orders if o.order_id == item.order_id), None)
            if not order or order.status in (OrderStatus.COMPLETED, OrderStatus.FAILED_SPOILED):
                # Orphan item from terminated order: discard/restock without occupying buffer
                return True

            if not self.feature_rob and order.status == OrderStatus.PACKING:
                # Without ROB: Item directly arrives at stalled packaging CPU bay
                item.status = ItemStatus.PACKING
                return True

            accepted = self.buffer_table.accept_item(item)
            if accepted:
                if order.status == OrderStatus.DISPATCHING:
                    order.status = OrderStatus.BUFFER_STAGING
            return accepted

        def on_spoiled(item: Item):
            order = next((o for o in self.orders if o.order_id == item.order_id), None)
            if order:
                self.abort_order(order, "Cold item melted during conveyor transit!")

        def on_deadlock(item: Item):
            order = next((o for o in self.orders if o.order_id == item.order_id), None)
            if order:
                self.abort_order(order, "Buffer table deadlock! Conveyor jammed waiting for slot.")

        self.storage_pipeline.tick(
            dt,
            on_item_arrived=on_arrived,
            on_item_spoiled=on_spoiled,
            on_deadlock=on_deadlock,
        )

        # 4. Advance Buffer Table (decay + check spoiled)
        self.buffer_table.tick(dt)
        self.peak_buffer_usage = max(self.peak_buffer_usage, self.buffer_table.total_buffered_items)

        # Check for spoiled items waiting in buffer
        for order in self.orders:
            if order.status in (OrderStatus.BUFFER_STAGING, OrderStatus.READY_TO_PACK):
                if order.is_any_cold_spoiled:
                    self.abort_order(order, "Cold item melted in buffer table!")

        # 5. Reorder Buffer (ROB) Commit logic
        ready_orders: List[Order] = []
        for order in self.orders:
            if order.status == OrderStatus.BUFFER_STAGING:
                if self.feature_rob:
                    # Atomic ROB: Commit to packing CPU only when 100% of items have arrived
                    if self.buffer_table.is_order_complete(order):
                        order.status = OrderStatus.READY_TO_PACK
                        self.log(f"✅ [Order {order.order_id}] All items arrived at Buffer Table (ROB Ready)")
                else:
                    # No ROB: Eagerly push to packaging CPU as soon as any item arrives, stalling the core
                    if self.buffer_table.staging.get(order.order_id):
                        order.status = OrderStatus.READY_TO_PACK
            if order.status == OrderStatus.READY_TO_PACK:
                ready_orders.append(order)

        # 6. Packaging Station CPU Scheduling
        idle_cores = self.cpu.idle_cores
        while idle_cores and ready_orders:
            next_order = self.cpu.select_next_order(ready_orders)
            if not next_order:
                break
            ready_orders.remove(next_order)
            core = idle_cores.pop(0)

            # Atomically transfer items from buffer table to packaging CPU core
            self.buffer_table.release_order_items(next_order)
            self.cpu.assign_order(core, next_order, self.sim_time)
            slack_disp = format_time_minutes(next_order.slack_time) if next_order.cold_items else "∞"
            self.log(f"⚙️ [Core {core.core_id}] Scheduled Order {next_order.order_id} (Slack: {slack_disp})")

        # 7. Advance CPU Packaging cores
        completed, cpu_spoiled = self.cpu.tick(dt, self.sim_time)
        for comp in completed:
            self.completed_orders.append(comp)
            self.log(f"🎉 [Order {comp.order_id}] PACKED & DISPATCHED successfully!")
        for sp in cpu_spoiled:
            self.spoiled_orders.append(sp)
            self.log(f"💥 [Order {sp.order_id}] FAILED! Cold item melted mid-packaging!")

    def get_metrics(self) -> Dict[str, Any]:
        total = len(self.orders)
        comp = len(self.completed_orders)
        spoil = len(self.spoiled_orders)
        spoil_pct = (spoil / total * 100.0) if total > 0 else 0.0

        latencies = [
            (o.completed_at - o.created_at)
            for o in self.completed_orders
            if o.completed_at is not None
        ]
        avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0

        total_busy = sum(core.busy_time_total for core in self.cpu.cores)
        cpu_util = (total_busy / (len(self.cpu.cores) * self.sim_time) * 100.0) if self.sim_time > 0 else 0.0

        return {
            "mode": self.mode,
            "sim_time": self.sim_time,
            "total_orders": total,
            "completed": comp,
            "spoiled": spoil,
            "spoil_percentage": spoil_pct,
            "avg_latency": avg_latency,
            "cpu_utilization": cpu_util,
            "peak_buffer_usage": self.peak_buffer_usage,
            "buffer_capacity": self.buffer_table.capacity,
        }
