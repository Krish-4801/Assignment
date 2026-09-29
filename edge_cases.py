"""
Edge Cases Simulation Suite for the Cold & Shelf Order Fulfillment System.
Tests and verifies every boundary condition, race condition, and contention scenario.
"""
from typing import List, Dict, Any, Tuple
from models import Item, ItemType, ItemStatus, Order, OrderStatus, format_time_minutes
from simulation import Simulation
from main import run_headless
from tui import RESET, BOLD, GREEN, RED, YELLOW, CYAN, MAGENTA


def run_sim_ticks(sim: Simulation, max_ticks: int = 1000, dt: float = 0.05) -> Simulation:
    """Helper to step simulation until finished or max_ticks reached."""
    ticks = 0
    while not sim.is_finished and ticks < max_ticks:
        sim.tick(dt)
        ticks += 1
    return sim


def simulate_pure_cold_orders() -> Dict[str, Any]:
    """
    Edge Case 1: Pure Cold Orders (0% Shelf Items).
    Tests whether JIT Phase 2 triggers immediately without waiting for non-existent shelf items.
    """
    orders = [
        Order(
            order_id=f"COLD-{i:02d}",
            items=[
                Item(item_id=f"C{i}-1", order_id=f"COLD-{i:02d}", item_type=ItemType.COLD, max_ttl=3.5, remaining_ttl=3.5, transit_duration=0.5),
                Item(item_id=f"C{i}-2", order_id=f"COLD-{i:02d}", item_type=ItemType.COLD, max_ttl=3.2, remaining_ttl=3.2, transit_duration=0.6),
            ],
            created_at=0.0,
            pack_duration=0.6,
        )
        for i in range(1, 5)
    ]

    sim = Simulation(custom_orders=orders, mode="ROBUST", num_cores=2, buffer_capacity=10)
    run_sim_ticks(sim)
    m = sim.get_metrics()
    return {
        "title": "1. Pure Cold Orders (0% Shelf Items)",
        "desc": "Tests if JIT immediately bypasses Phase 1 and avoids waiting for shelf items",
        "passed": (m["completed"] == 4 and m["spoiled"] == 0),
        "details": f"Delivered: {m['completed']}/4 | Spoiled: {m['spoiled']} | Time: {format_time_minutes(m['sim_time'])}",
    }


def simulate_pure_shelf_orders() -> Dict[str, Any]:
    """
    Edge Case 2: Pure Shelf Orders (0% Cold Items).
    Tests infinite slack handling, CPU scheduling, and non-perishable completion.
    """
    orders = [
        Order(
            order_id=f"SHELF-{i:02d}",
            items=[
                Item(item_id=f"S{i}-1", order_id=f"SHELF-{i:02d}", item_type=ItemType.SHELF, max_ttl=9999.0, remaining_ttl=9999.0, transit_duration=0.8),
                Item(item_id=f"S{i}-2", order_id=f"SHELF-{i:02d}", item_type=ItemType.SHELF, max_ttl=9999.0, remaining_ttl=9999.0, transit_duration=1.0),
            ],
            created_at=0.0,
            pack_duration=0.5,
        )
        for i in range(1, 5)
    ]

    sim = Simulation(custom_orders=orders, mode="ROBUST", num_cores=2, buffer_capacity=10)
    run_sim_ticks(sim)
    m = sim.get_metrics()
    return {
        "title": "2. Pure Shelf Orders (0% Cold Items)",
        "desc": "Tests infinite slack time (Slack = ∞) and non-perishable scheduling without starvation",
        "passed": (m["completed"] == 4 and m["spoiled"] == 0),
        "details": f"Delivered: {m['completed']}/4 | Spoiled: {m['spoiled']} | Time: {format_time_minutes(m['sim_time'])}",
    }


def simulate_fragmented_buffer_deadlock() -> Dict[str, Any]:
    """
    Edge Case 3: Fragmented Buffer Deadlock (Dining Philosophers).
    Buffer has 6 slots. 3 orders with 3 items each arrive simultaneously (9 items total).
    In Naive mode, each order gets 2 items into the buffer, 100% filling it, so no order completes!
    In Robust mode with Slot Reservation, orders are admitted sequentially, avoiding deadlock.
    """
    def make_orders():
        return [
            Order(
                order_id=f"DEADLOCK-{i}",
                items=[
                    Item(item_id=f"D{i}-S1", order_id=f"DEADLOCK-{i}", item_type=ItemType.SHELF, max_ttl=9999, remaining_ttl=9999, transit_duration=0.5),
                    Item(item_id=f"D{i}-S2", order_id=f"DEADLOCK-{i}", item_type=ItemType.SHELF, max_ttl=9999, remaining_ttl=9999, transit_duration=0.6),
                    Item(item_id=f"D{i}-C3", order_id=f"DEADLOCK-{i}", item_type=ItemType.COLD, max_ttl=3.5, remaining_ttl=3.5, transit_duration=0.7),
                ],
                created_at=0.0,
                pack_duration=0.5,
            )
            for i in range(1, 4)
        ]

    # Naive Admission run (No Slot Reservation: 3 orders partially take 2 slots each -> 6/6 slots filled -> deadlock!)
    sim_naive = Simulation(
        custom_orders=make_orders(),
        mode="CUSTOM",
        feature_rob=True,
        feature_admission=False,
        feature_jit=False,
        num_cores=1,
        buffer_capacity=6,
    )
    run_sim_ticks(sim_naive, max_ticks=300)
    naive_m = sim_naive.get_metrics()

    # Robust run (Slot Reservation enabled: admits max 2 orders to fit 6 slots safely)
    sim_robust = Simulation(
        custom_orders=make_orders(),
        mode="ROBUST",
        num_cores=1,
        buffer_capacity=6,
    )
    run_sim_ticks(sim_robust, max_ticks=300)
    robust_m = sim_robust.get_metrics()

    passed = (robust_m["completed"] == 3 and naive_m["spoiled"] > 0)
    return {
        "title": "3. Fragmented Buffer Deadlock (Dining Philosophers)",
        "desc": "Small buffer (6 slots) flooded by 3 orders (3 items each). Without Slot Reservation, buffer deadlocks; with it, orders complete.",
        "passed": passed,
        "details": f"Without Reservation: {naive_m['completed']}/3 delivered, {naive_m['spoiled']} spoiled | With Reservation: {robust_m['completed']}/3 delivered, {robust_m['spoiled']} spoiled",
    }


def simulate_extreme_transit_asymmetry() -> Dict[str, Any]:
    """
    Edge Case 4: Extreme Transit Asymmetry (Delayed Shelf vs Fast Cold).
    Shelf item takes 1.8 min; Cold item takes 0.3 min.
    Tests if JIT delays the fast cold item so it does not arrive 1.5 min early and melt in buffer.
    """
    orders = [
        Order(
            order_id="ASYMM-01",
            items=[
                Item(item_id="ASYMM-SHELF", order_id="ASYMM-01", item_type=ItemType.SHELF, max_ttl=9999, remaining_ttl=9999, transit_duration=1.8),
                Item(item_id="ASYMM-COLD", order_id="ASYMM-01", item_type=ItemType.COLD, max_ttl=3.0, remaining_ttl=3.0, transit_duration=0.3),
            ],
            created_at=0.0,
            pack_duration=0.5,
        )
    ]

    sim = Simulation(custom_orders=orders, mode="ROBUST", num_cores=1, buffer_capacity=6)
    run_sim_ticks(sim)
    m = sim.get_metrics()
    return {
        "title": "4. Extreme Transit Asymmetry (Slow Shelf 1.8m vs Fast Cold 0.3m)",
        "desc": "Tests JIT delayed cold dispatch preventing cold item from melting while waiting for distant shelf item",
        "passed": (m["completed"] == 1 and m["spoiled"] == 0),
        "details": f"Delivered: {m['completed']}/1 | Cold Freshness Remaining: {orders[0].cold_items[0].freshness_percentage:.1f}%",
    }


def simulate_intra_order_thermal_disparity() -> Dict[str, Any]:
    """
    Edge Case 5: Intra-Order Thermal Disparity (Varying Melting Rates in Same Order).
    Order A has a high-decay item (TTL 3.0 min) and low-decay item (TTL 5.0 min).
    Order B has moderate-decay items (TTL 4.0 min).
    Tests if LST prioritizes Order A based on its MINIMUM cold TTL, rather than average.
    """
    order_a = Order(
        order_id="ORD-FRAGILE",
        items=[
            Item(item_id="FA-1", order_id="ORD-FRAGILE", item_type=ItemType.COLD, max_ttl=3.0, remaining_ttl=3.0, transit_duration=0.5),
            Item(item_id="FA-2", order_id="ORD-FRAGILE", item_type=ItemType.COLD, max_ttl=5.0, remaining_ttl=5.0, transit_duration=0.5),
        ],
        created_at=0.0,
        pack_duration=0.5,
    )
    order_b = Order(
        order_id="ORD-MODERATE",
        items=[
            Item(item_id="FB-1", order_id="ORD-MODERATE", item_type=ItemType.COLD, max_ttl=4.0, remaining_ttl=4.0, transit_duration=0.5),
        ],
        created_at=0.0,
        pack_duration=0.5,
    )

    sim = Simulation(custom_orders=[order_b, order_a], mode="ROBUST", num_cores=1, buffer_capacity=6)
    run_sim_ticks(sim)
    m = sim.get_metrics()

    # Verify that ORD-FRAGILE was scheduled first because of its 3.0 min item
    first_completed = sim.completed_orders[0].order_id if sim.completed_orders else None
    passed = (m["completed"] == 2 and m["spoiled"] == 0 and first_completed == "ORD-FRAGILE")
    return {
        "title": "5. Intra-Order Thermal Disparity (3.0m vs 5.0m in same order)",
        "desc": "Verifies LST computes slack using strictly the minimum TTL, scheduling ORD-FRAGILE before ORD-MODERATE",
        "passed": passed,
        "details": f"First Scheduled/Delivered: {first_completed} | Spoilage: {m['spoiled']}",
    }


def simulate_mid_packaging_spoilage() -> Dict[str, Any]:
    """
    Edge Case 6: Mid-Packaging Spoilage (Ambient Melting inside Packaging Station).
    An order reaches the packaging table with remaining TTL (0.3 min) LESS than pack duration (0.8 min).
    Verifies that the packaging CPU detects the melt, aborts immediately, discards items,
    and cleanly resets the core to IDLE without locking up.
    """
    doomed_order = Order(
        order_id="ORD-DOOMED",
        items=[
            Item(item_id="D-1", order_id="ORD-DOOMED", item_type=ItemType.COLD, max_ttl=1.0, remaining_ttl=0.3, transit_duration=0.1),
        ],
        created_at=0.0,
        pack_duration=0.8,  # Requires 0.8 min, but only 0.3 min left!
    )
    healthy_order = Order(
        order_id="ORD-HEALTHY",
        items=[
            Item(item_id="H-1", order_id="ORD-HEALTHY", item_type=ItemType.COLD, max_ttl=3.5, remaining_ttl=3.5, transit_duration=0.1),
        ],
        created_at=0.0,
        pack_duration=0.5,
    )

    sim = Simulation(custom_orders=[doomed_order, healthy_order], mode="ROBUST", num_cores=1, buffer_capacity=6)
    run_sim_ticks(sim)
    m = sim.get_metrics()

    passed = (m["spoiled"] == 1 and m["completed"] == 1 and doomed_order.status == OrderStatus.FAILED_SPOILED)
    return {
        "title": "6. Mid-Packaging Spoilage (Ambient Melting on Packaging Table)",
        "desc": "Verifies CPU core detects spoilage mid-pack, aborts cleanly, and subsequent healthy order completes",
        "passed": passed,
        "details": f"Doomed Status: {doomed_order.status.value} | Healthy Completed: {healthy_order.status.value}",
    }


def simulate_orphan_influx_cleanup() -> Dict[str, Any]:
    """
    Edge Case 7: Orphan Influx Cleanup.
    Order has 1 fast cold item (transit 0.2 min) and 1 slow shelf item (transit 1.2 min).
    Cold item spoils before order can pack. Order is aborted.
    When the late shelf item finally arrives, tests that buffer table rejects it (no leaked slots).
    """
    order = Order(
        order_id="ORD-ORPHAN",
        items=[
            Item(item_id="ORPH-COLD", order_id="ORD-ORPHAN", item_type=ItemType.COLD, max_ttl=0.4, remaining_ttl=0.4, transit_duration=0.2),
            Item(item_id="ORPH-SHELF", order_id="ORD-ORPHAN", item_type=ItemType.SHELF, max_ttl=9999, remaining_ttl=9999, transit_duration=1.2),
        ],
        created_at=0.0,
        pack_duration=0.5,
    )

    sim = Simulation(custom_orders=[order], mode="NAIVE", num_cores=1, buffer_capacity=6)
    run_sim_ticks(sim)

    # After simulation completes, buffer table must be completely empty (0 orphan slots leaked)
    buffer_empty = (sim.buffer_table.total_buffered_items == 0)
    passed = (order.status == OrderStatus.FAILED_SPOILED and buffer_empty)
    return {
        "title": "7. Orphan Item Influx Post-Cancellation",
        "desc": "Late-arriving shelf item from a dead order must be discarded without permanently leaking a buffer slot",
        "passed": passed,
        "details": f"Order Status: {order.status.value} | Final Buffer Leak: {sim.buffer_table.total_buffered_items} slots",
    }


def simulate_oversized_order() -> Dict[str, Any]:
    """
    Edge Case 8: Oversized Order Exceeding Total Buffer Capacity (N_items > Capacity).
    Buffer capacity is 6 slots. Order requests 8 items.
    Verifies admission control detects that order can never fit into buffer and avoids deadlock.
    """
    oversized = Order(
        order_id="ORD-OVERSIZED",
        items=[
            Item(item_id=f"OV-{i}", order_id="ORD-OVERSIZED", item_type=ItemType.SHELF, max_ttl=9999, remaining_ttl=9999, transit_duration=0.5)
            for i in range(8)
        ],
        created_at=0.0,
        pack_duration=0.5,
    )

    sim = Simulation(custom_orders=[oversized], mode="ROBUST", num_cores=1, buffer_capacity=6)
    # Run a few ticks: admission control should hold order safely in storage rather than half-filling buffer
    for _ in range(50):
        sim.tick(0.05)

    buffer_empty = (sim.buffer_table.total_buffered_items == 0)
    order_held = (oversized.status == OrderStatus.PENDING)
    passed = (buffer_empty and order_held)
    return {
        "title": "8. Oversized Order Exceeding Buffer Capacity (8 items in 6-slot buffer)",
        "desc": "Admission control prevents partially dispatching an order that can never fit into the buffer",
        "passed": passed,
        "details": f"Buffer Slots Used: {sim.buffer_table.total_buffered_items}/6 | Order State: {oversized.status.value}",
    }


def simulate_admission_failure_transit_asymmetry() -> Dict[str, Any]:
    """
    Edge Case 9: Where Admission Control FAILS without JIT & Chilled Buffer.
    Scenario: Shelf items take 2.4 min (distant aisle), Cold items take 0.5 min (nearby locker).
    Cold item TTL is 3.0 min. Packaging takes 0.8 min.
    - Only Admission Control: Dispatches both items immediately at t=0.
      Cold item waits 1.9 min in unchilled buffer. Remaining TTL drops to 0.6 min.
      Pack needs 0.8 min -> Cold item melts on packaging table! (100% Spoilage)
    - Full Robust: JIT delays cold dispatch until shelf is near buffer table.
      Cold item arrives fresh with 2.5 min TTL left -> Packs successfully! (0% Spoilage)
    """
    def make_orders():
        return [
            Order(
                order_id=f"ASYMM-FAIL-{i:02d}",
                items=[
                    Item(item_id=f"S{i}", order_id=f"ASYMM-FAIL-{i:02d}", item_type=ItemType.SHELF, max_ttl=9999, remaining_ttl=9999, transit_duration=2.4),
                    Item(item_id=f"C{i}", order_id=f"ASYMM-FAIL-{i:02d}", item_type=ItemType.COLD, max_ttl=3.0, remaining_ttl=3.0, transit_duration=0.5),
                ],
                created_at=0.0,
                pack_duration=0.8,
            )
            for i in range(1, 6)
        ]

    sim_adm = Simulation(custom_orders=make_orders(), mode="NAIVE", feature_admission=True, num_cores=2, buffer_capacity=10)
    run_sim_ticks(sim_adm, max_ticks=400)
    m_adm = sim_adm.get_metrics()

    sim_rob = Simulation(custom_orders=make_orders(), mode="ROBUST", num_cores=2, buffer_capacity=10)
    run_sim_ticks(sim_rob, max_ticks=400)
    m_rob = sim_rob.get_metrics()

    passed = (m_adm["spoiled"] == 5 and m_rob["completed"] == 5 and m_rob["spoiled"] == 0)
    return {
        "title": "9. Where Admission Control Alone FAILS: Long Shelf Transit (2.4m)",
        "desc": "Without JIT, cold item sits 1.9m in buffer waiting for shelf item and melts mid-pack. Full Robust succeeds 100%.",
        "passed": passed,
        "details": f"Only Admission Control: {m_adm['completed']}/5 delivered, {m_adm['spoiled']}/5 spoiled ({m_adm['spoil_percentage']:.0f}% loss) | Full Robust: {m_rob['completed']}/5 delivered, {m_rob['spoiled']}/5 spoiled",
    }


def simulate_admission_failure_priority_inversion() -> Dict[str, Any]:
    """
    Edge Case 10: Where Admission Control FAILS without LST (Priority Inversion).
    Scenario: Single packaging core.
    Order 1: Non-perishable shelf order requiring long pack time (2.2 min, infinite slack).
    Order 2: Urgent cold order (pack time = 0.5 min, cold remaining TTL = 2.0 min).
    - Admission Control + FIFO CPU: Core 0 grabs Order 1 first. Order 2 waits 2.2 min.
      Order 2's cold item melts in buffer at 2.0 min!
    - Full Robust (LST Scheduler): Core prioritizes Order 2 (Slack: 1.5 min) ahead of Order 1 (Slack: ∞).
      Order 2 packs in 0.5 min safely, then Order 1 packs. 0% Spoilage!
    """
    def make_orders():
        return [
            Order(order_id="LONG-SHELF", items=[Item(item_id="S1", order_id="LONG-SHELF", item_type=ItemType.SHELF, max_ttl=9999, remaining_ttl=9999, transit_duration=0.5)], created_at=0.0, pack_duration=2.2),
            Order(order_id="URGENT-COLD", items=[Item(item_id="C1", order_id="URGENT-COLD", item_type=ItemType.COLD, max_ttl=2.0, remaining_ttl=2.0, transit_duration=0.5)], created_at=0.0, pack_duration=0.5),
        ]

    sim_fifo = Simulation(custom_orders=make_orders(), mode="CUSTOM", feature_admission=True, feature_lst=False, feature_jit=True, feature_chilled_buffer=False, num_cores=1, buffer_capacity=6)
    run_sim_ticks(sim_fifo, max_ticks=200)
    m_fifo = sim_fifo.get_metrics()

    sim_lst = Simulation(custom_orders=make_orders(), mode="ROBUST", num_cores=1, buffer_capacity=6)
    run_sim_ticks(sim_lst, max_ticks=200)
    m_lst = sim_lst.get_metrics()

    passed = (m_fifo["spoiled"] == 1 and m_lst["completed"] == 2 and m_lst["spoiled"] == 0)
    return {
        "title": "10. Where Admission Control Alone FAILS: FIFO Priority Inversion",
        "desc": "Long shelf order blocks single packaging core; urgent cold order melts in buffer under FIFO. LST schedules urgent order first.",
        "passed": passed,
        "details": f"Admission Control + FIFO: {m_fifo['completed']}/2 delivered, {m_fifo['spoiled']} spoiled | Full Robust (LST): {m_lst['completed']}/2 delivered, {m_lst['spoiled']} spoiled",
    }


def simulate_rush_hour_congestion() -> Dict[str, Any]:
    """
    Edge Case 11: Rush Hour Bottleneck (Packaging times doubled to 1.0-2.0 min).
    Tests that JIT staggered dispatch and dynamic admission control adapt to the degraded
    CPU packaging throughput without causing buffer pileups or cold spoilage.
    """
    sim_naive = Simulation(num_orders=15, mode="NAIVE", num_cores=2, buffer_capacity=10, rush_hour=True, seed=42)
    run_sim_ticks(sim_naive, max_ticks=600)
    m_naive = sim_naive.get_metrics()

    sim_rob = Simulation(num_orders=15, mode="ROBUST", num_cores=2, buffer_capacity=10, rush_hour=True, seed=42)
    run_sim_ticks(sim_rob, max_ticks=600)
    m_rob = sim_rob.get_metrics()

    passed = (m_naive["spoiled"] > 0 and m_rob["completed"] == 15 and m_rob["spoiled"] == 0)
    return {
        "title": "11. Rush Hour Packaging Bottleneck (Pack Duration Doubled to 1.0-2.0m)",
        "desc": "Packaging worker throughput cut by 50%. Naive experiences severe spoilage; Robust holds 0% spoilage.",
        "passed": passed,
        "details": f"Naive Rush Spoilage: {m_naive['spoiled']}/15 ({m_naive['spoil_percentage']:.0f}%) | Robust Rush: {m_rob['completed']}/15 delivered, {m_rob['spoiled']} spoiled",
    }


def run_all_edge_cases():
    """Executes all edge cases and prints a structured verification report."""
    print("\n" + "=" * 92)
    print(f"{BOLD}{CYAN}             ORDER FULFILLMENT SIMULATION: EDGE CASE VERIFICATION SUITE{RESET}")
    print("=" * 92)

    tests = [
        simulate_pure_cold_orders,
        simulate_pure_shelf_orders,
        simulate_fragmented_buffer_deadlock,
        simulate_extreme_transit_asymmetry,
        simulate_intra_order_thermal_disparity,
        simulate_mid_packaging_spoilage,
        simulate_orphan_influx_cleanup,
        simulate_oversized_order,
        simulate_admission_failure_transit_asymmetry,
        simulate_admission_failure_priority_inversion,
        simulate_rush_hour_congestion,
    ]

    all_passed = True
    for i, test_fn in enumerate(tests, 1):
        res = test_fn()
        status_badge = f"{GREEN}[PASSED]{RESET}" if res["passed"] else f"{RED}[FAILED]{RESET}"
        if not res["passed"]:
            all_passed = False

        print(f"\n{BOLD}{res['title']}{RESET} {status_badge}")
        print(f"  {CYAN}Scenario:{RESET} {res['desc']}")
        print(f"  {YELLOW}Outcome:{RESET}  {res['details']}")

    print("\n" + "=" * 92)
    if all_passed:
        print(f"{BOLD}{GREEN}✔ ALL {len(tests)} EDGE CASES PASSED! System state machine and boundary handling are robust.{RESET}\n")
    else:
        print(f"{BOLD}{RED}✖ SOME EDGE CASES FAILED. Review details above.{RESET}\n")


if __name__ == "__main__":
    run_all_edge_cases()
