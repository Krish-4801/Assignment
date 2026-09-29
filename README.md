# 🛒 Cold & Shelf Fulfillment Simulation (CPU & Buffer Architecture)

A CLI discrete-event simulation of an automated warehouse order fulfillment system that solves the perishability, synchronization, and buffer deadlock problems using **Computer Architecture (CPU & OS)** principles.

---

## 🏗️ Architecture & Concepts

```
[ Order Generator ] (N orders, random % Cold vs Shelf)
         │
         ▼
[ Admission Controller & JIT Scheduler ]
   │                                   │
   ▼ (Delayed dispatch)                ▼ (Immediate dispatch)
[ Cold Storage ]                    [ Shelf Storage ]
   │ (Melts in transit)                │ (Non-perishable)
   └───────────────┬───────────────────┘
                   ▼
       [ Buffer Tables / ROB ]
       - Chilled Buffer (Cold items: 3x slower melt)
       - Shelf Buffer (Ambient items)
       - Atomic Assembly Lock (Waits until all items ready)
                   │
                   ▼ (Atomic Commit: Lowest Slack Time First)
       [ Packaging Station "CPU" ]
       - Core 0..M (Packaging Workers)
       - Least Slack Time (LST / EDF) Scheduling
                   │
                   ▼
            [ Dispatched ]
```

### The Key Problems Solved

1. **Perishability & The Melting Window:** Cold items decay once removed from cold storage. If dispatched simultaneously with shelf items, they melt while waiting at the staging area.
2. **Buffer Table Deadlock:** Multiple partial orders occupy buffer slots waiting for remaining items; without admission control, the buffer table fills up completely, blocking any single order from finishing.
3. **Head-of-Line Blocking & Station Contention:** Packaging stations idling or blocked waiting for a missing part of an order.
4. **Priority Inversion:** Non-urgent shelf orders monopolizing packaging workers while fragile cold items melt in queue.

### The Solutions Implemented

* **Admission Control (Flow Control):** Bounds active in-flight orders based on CPU cores and buffer capacity. Unprocessed orders remain safely inside cold storage where items do not decay.
* **Just-In-Time (JIT) Staggered Dispatch:** Dispatches slow/ambient shelf items first; only dispatches cold items when shelf items are $\ge 70\%$ through transit to minimize ambient exposure.
* **Reorder Buffer (ROB) / Atomic Commit:** Orders are only promoted to the packaging CPU once $100\%$ of items have arrived and validated at the buffer table.
* **Chilled Buffer Table (L1 Cache):** Buffer table slots feature cooling protection, reducing the decay rate by $67\%$ ($0.33\times$ multiplier).
* **Least Slack Time (LST / EDF) CPU Scheduling:** Packaging CPU cores pick ready orders based on minimum slack time ($\text{TTL} - \text{Pack Time}$), prioritizing the most fragile cold items first.

---

## ⏱️ System Timings & Operational Parameters

All time dynamics in the simulation are modeled in **minutes** with randomized per-item transit:

| Parameter | Duration | Description |
| :--- | :--- | :--- |
| **Cold Item Melting Window (TTL)** | **3.0 – 4.0 minutes** (180s – 240s) | Lifespan outside cold storage before complete liquefaction / spoilage |
| **Cold Item Transit Time** | **0.4 – 0.9 minutes** (24s – 54s) | Randomized transit from dedicated cold storage lockers to buffer table |
| **Shelf Item Transit Time** | **0.6 – 1.4 minutes** (36s – 84s) | Randomized transit from ambient warehouse aisles to buffer table |
| **Packaging Duration** | **0.5 – 1.0 minutes** (30s – 60s) | Time required at a packaging CPU core to box, insulate, and tape an order |
| **Chilled Buffer Protection** | **$0.33\times$ decay rate** | Slows melting by $3\times$, extending cold lifespan to **9.0 – 12.0 minutes** |
| **Buffer Deadlock Jam Timeout** | **3.0 minutes** | Maximum allowable wait for conveyor backlog before declaring deadlock |
| **Display Format** | `MM:SS (X.Xm)` | Displayed across live dashboard, logs, and benchmark tables |

---

## 🚀 How to Run

### 1. Interactive Live Dashboard (Default)
Watch real-time countdowns, transit animations, buffer slot usage, and packaging CPU core utilization:
```bash
python3 main.py
```
* **Options:**
  * `--orders 20`: Number of orders (default: 15)
  * `--cores 3`: Number of packaging CPU cores (default: 2)
  * `--buffer 12`: Buffer table slot capacity (default: 12)
  * `--speed 2.0`: Speed multiplier (default: 1.0)

* **Interactive Controls:**
  * `[Space]`: Pause / Resume simulation
  * `[+]` / `[-]`: Speed up / Slow down simulation
  * `[S]`: Single-step one tick
  * `[Q]`: Quit

### 2. Comparative Benchmark (Naive vs. Robust)
Runs both algorithms on the exact same randomized seed and outputs a side-by-side performance table:
```bash
python3 main.py --mode compare --orders 20 --seed 42
```

### 3. Feature Ablation Study (Isolated Feature Comparison)
Isolates and tests each architectural feature independently to demonstrate its specific contribution:
```bash
python3 main.py --mode ablation --orders 20 --seed 42
```

### 4. Rush Hour Congestion Benchmark
Simulates peak rush hour conditions where packaging station crowding and worker fatigue double the packaging time to 1.0 – 2.0 min:
```bash
python3 main.py --mode rush-hour --orders 20 --seed 123
```

### 5. Edge Cases Verification Suite
Runs the automated test suite covering all 11 boundary scenarios, race conditions, and contention states:
```bash
python3 main.py --mode edge-cases
# or directly:
python3 edge_cases.py
```

---

## 🧪 Edge Cases Test Matrix

| # | Edge Case Scenario | Condition Tested | System Behavior & Mitigation |
| :--- | :--- | :--- | :--- |
| **1** | **Pure Cold Orders (0% Shelf)** | No shelf items to trigger Phase 1 | JIT immediately bypasses Phase 1; dispatches cold items without waiting. |
| **2** | **Pure Shelf Orders (0% Cold)** | Infinite slack time ($\text{Slack} = \infty$) | Handled gracefully without starvation; completed under CPU scheduling. |
| **3** | **Fragmented Buffer Deadlock** | 3 orders $\times$ 3 items into 6-slot buffer | **Slot Reservation**: Admits max 2 orders; avoids Dining Philosophers jam. |
| **4** | **Extreme Transit Asymmetry** | Shelf takes 1.8m, Cold takes 0.3m | JIT delays cold dispatch; cold item freshness preserved $\ge 68\%$. |
| **5** | **Intra-Order Thermal Disparity** | Item A (3.0m TTL) vs Item B (5.0m TTL) | LST tracks **minimum** TTL; schedules order before moderate-decay orders. |
| **6** | **Mid-Packaging Spoilage** | Cold TTL (0.3m) < Pack duration (0.8m) | Core aborts mid-pack, discards items, and cleans bay without core hang. |
| **7** | **Orphan Influx Cleanup** | Order fails while shelf item is en route | Arriving late item rejected/restocked; **zero buffer slots leaked**. |
| **8** | **Oversized Order** | Order with 8 items into 6-slot buffer | Admission control holds order in pending; avoids half-filling buffer. |
| **9** | **Admission Alone Fails: High Transit Asymmetry** | Shelf takes 2.4m, Cold takes 0.5m | Without JIT, cold item melts in buffer. **Admission: 0% delivered vs. Robust: 100% delivered.** |
| **10** | **Admission Alone Fails: Priority Inversion** | Long shelf order (2.2m) blocks urgent cold order | Under FIFO, cold order melts waiting. **LST schedules urgent order first $\to$ 0% spoilage.** |
| **11** | **Rush Hour Packaging Bottleneck** | Pack times doubled to 1.0 – 2.0 min | Dynamic admission limits in-flight load; **Naive: 73% spoilage vs. Robust: 0% spoilage.** |

---

## ⚡ Rush Hour Benchmark Results (20 Orders, Seed: 123)

| Configuration | Delivered | Spoiled | Spoilage % | Avg Latency |
| :--- | :--- | :--- | :--- | :--- |
| **1. Naive (FIFO + Ambient Buffer)** | 5/20 | 15 | **75.0%** | 04:47 (4.8m) |
| **2. Only Admission Control** | 18/20 | 2 | **10.0%** | 14:00 (14.0m) |
| **3. Robust System (JIT + LST + Chilled ROB)** | **20/20** | **0** | **0.0%** | 14:36 (14.6m) |

---

## 📊 Feature Ablation Study Results (20 Orders, Seed: 42)

| Configuration / Feature | Delivered | Spoiled | Spoilage % | Avg Latency | Peak Buffer Slots |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Baseline (Naive FIFO)** | 7/20 | 13 | 65.0% | 02:48 (2.8m) | 12/12 (Jammed) |
| **2. Only JIT Dispatch** | 9/20 | 11 | 55.0% | 02:58 (3.0m) | 12/12 (Jammed) |
| **3. Only LST CPU Sched** | 7/20 | 13 | 65.0% | 02:48 (2.8m) | 12/12 (Jammed) |
| **4. Only Chilled Buffer ROB** | 3/20 | 17 | 85.0% | 04:41 (4.7m) | 12/12 (Jammed) |
| **5. Only Admission Control** | 20/20 | 0 | **0.0%** | 05:56 (5.9m) | 8/12 |
| **6. Full Robust (All Combined)** | **20/20** | **0** | **0.0%** | 06:18 (6.3m) | **8/12 (Optimal)** |

### Architectural Insights from the Ablation Study:
1. **Admission Control is the foundation:** Without admission control, unconstrained orders flood the pipeline, causing buffer table deadlock and conveyor jams regardless of scheduling policy.
2. **JIT Dispatch alone reduces spoilage:** Even under congestion, staggered dispatch improves delivery from 7/20 to 9/20 by holding cold items in lockers until shelf items are en route.
3. **Harmonized Pipeline:** Combining Admission Control + JIT + Chilled Buffer ROB + LST eliminates all spoilage (**0% spoilage**), limits peak buffer usage to **8/12 slots**, and guarantees smooth out-of-order execution.
