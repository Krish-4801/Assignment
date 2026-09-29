"""
Packaging Station modelled as a multi-core CPU.
Implements CPU scheduling algorithms: FIFO vs Least Slack Time (LST / EDF).
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple
from models import Order, OrderStatus, ItemStatus


@dataclass
class CPUCore:
    core_id: int
    current_order: Optional[Order] = None
    busy_time_total: float = 0.0

    @property
    def is_idle(self) -> bool:
        return self.current_order is None

    @property
    def progress_percentage(self) -> float:
        if not self.current_order or self.current_order.pack_duration <= 0:
            return 0.0
        return min(100.0, (self.current_order.pack_progress / self.current_order.pack_duration) * 100.0)


class PackagingStationCPU:
    def __init__(self, num_cores: int = 2, scheduling_policy: str = "LST"):
        self.cores = [CPUCore(core_id=i) for i in range(num_cores)]
        self.scheduling_policy = scheduling_policy.upper()  # 'LST' or 'FIFO'

    @property
    def idle_cores(self) -> List[CPUCore]:
        return [core for core in self.cores if core.is_idle]

    def select_next_order(self, ready_orders: List[Order]) -> Optional[Order]:
        """
        Picks the best order from ready_orders according to scheduling policy.
        - FIFO: Earliest created order
        - LST (Least Slack Time): Order with minimum slack time (urgency)
        """
        if not ready_orders:
            return None

        if self.scheduling_policy == "FIFO":
            return ready_orders[0]

        # LST / EDF: Order with smallest slack time
        # Slack = (Cold remaining TTL) - (Packing time left)
        # Orders with expiring cold items get scheduled first!
        return min(ready_orders, key=lambda o: (o.slack_time, o.created_at))

    def assign_order(self, core: CPUCore, order: Order, sim_time: float):
        core.current_order = order
        order.status = OrderStatus.PACKING
        order.started_packing_at = sim_time

    def tick(self, dt: float, sim_time: float) -> Tuple[List[Order], List[Order]]:
        """
        Advances the CPU cores by dt.
        Returns: (completed_orders, spoiled_during_packing_orders)
        """
        completed = []
        spoiled = []

        for core in self.cores:
            if core.is_idle:
                continue

            order = core.current_order
            core.busy_time_total += dt

            # Cold items decay at ambient packing rate while on the packaging table
            for item in order.cold_items:
                item.tick_decay(dt, in_chilled_buffer=False)

            # Check if any cold item spoiled mid-packing
            if order.is_any_cold_spoiled:
                order.status = OrderStatus.FAILED_SPOILED
                for item in order.items:
                    item.status = ItemStatus.SPOILED if item.is_spoiled else ItemStatus.IN_STORAGE
                spoiled.append(order)
                core.current_order = None
                continue

            # If order is not fully assembled, CPU core stalls waiting for missing operands!
            if not order.is_fully_assembled:
                continue

            order.pack_progress += dt

            # Check if order packaging is finished
            if order.pack_progress >= order.pack_duration:
                order.status = OrderStatus.COMPLETED
                order.completed_at = sim_time
                for item in order.items:
                    item.status = ItemStatus.PACKED
                completed.append(order)
                core.current_order = None

        return completed, spoiled
