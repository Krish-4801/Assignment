"""
Terminal User Interface (TUI) for the Order Fulfillment Simulation.
Uses standard ANSI escape codes with zero external dependencies.
"""
import sys
import os
import select
import termios
import tty
from typing import Optional
from models import OrderStatus, ItemType, format_time_minutes
from simulation import Simulation


# ANSI Color Codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"
BG_BLUE = "\033[44m"
BG_DARK = "\033[40m"


def make_bar(percent: float, width: int = 10, color: str = GREEN) -> str:
    """Renders a progress bar like [██████░░░░] 60%"""
    clamped = max(0.0, min(100.0, percent))
    filled = int(round((clamped / 100.0) * width))
    empty = width - filled
    bar_str = "█" * filled + "░" * empty
    return f"{color}[{bar_str}]{RESET} {clamped:5.1f}%"


class KeyListener:
    """Non-blocking keyboard reader for POSIX terminals."""
    def __init__(self):
        self.old_settings = None

    def __enter__(self):
        try:
            self.old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.old_settings is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)
            except Exception:
                pass

    def get_key(self) -> Optional[str]:
        try:
            rlist, _, _ = select.select([sys.stdin], [], [], 0.0)
            if rlist:
                return sys.stdin.read(1)
        except Exception:
            pass
        return None


class Dashboard:
    def __init__(self, sim: Simulation):
        self.sim = sim

    def render(self) -> str:
        lines = []
        metrics = self.sim.get_metrics()

        # Clear screen and move cursor to top
        lines.append("\033[H\033[2J")

        # 1. Header Bar
        mode_color = GREEN if self.sim.mode == "ROBUST" else YELLOW
        pause_str = f"{RED}[PAUSED]{RESET}" if self.sim.is_paused else f"{GREEN}[RUNNING]{RESET}"
        lines.append(f"{BOLD}{BG_BLUE}{WHITE} 🛒 COLD & SHELF FULFILLMENT SIMULATION — CPU & BUFFER ARCHITECTURE {RESET}")
        rush_tag = f" | {RED}{BOLD}[RUSH HOUR: {self.sim.pack_time_multiplier:.1f}x PACK TIME]{RESET}" if self.sim.rush_hour else ""
        lines.append(
            f" Mode: {mode_color}{BOLD}{self.sim.mode}{RESET} | "
            f"Sim Time: {CYAN}{format_time_minutes(self.sim.sim_time)} ({self.sim.sim_time:4.2f}m){RESET} | "
            f"Speed: {BOLD}{self.sim.time_scale:.1f}x{RESET} | "
            f"Status: {pause_str}{rush_tag}"
        )
        lines.append(f"{DIM} Controls: [Space] Pause/Resume | [+/-] Speed | [Q] Quit | [S] Step{RESET}")
        lines.append("─" * 80)

        # 2. Key Metrics Bar
        spoil_color = RED if metrics["spoiled"] > 0 else GREEN
        lines.append(
            f"{BOLD}📊 METRICS:{RESET} "
            f"Total Orders: {metrics['total_orders']} | "
            f"Completed: {GREEN}{metrics['completed']}{RESET} | "
            f"Spoiled: {spoil_color}{metrics['spoiled']} ({metrics['spoil_percentage']:.1f}%){RESET} | "
            f"CPU Util: {CYAN}{metrics['cpu_utilization']:.1f}%{RESET}"
        )
        buffer_usage = self.sim.buffer_table.total_buffered_items
        buffer_cap = self.sim.buffer_table.capacity
        buf_color = RED if self.sim.buffer_table.utilization_percentage >= 80 else CYAN
        lines.append(
            f"Buffer Table Slots: {buf_color}{buffer_usage}/{buffer_cap}{RESET} "
            f"({self.sim.buffer_table.utilization_percentage:.0f}% occupied) | "
            f"Peak Buffer Load: {metrics['peak_buffer_usage']} slots"
        )
        lines.append("─" * 80)

        # 3. Packaging Station "CPU" Section
        lines.append(f"{BOLD}⚡ PACKAGING STATION (CPU CORES - Policy: {self.sim.cpu.scheduling_policy}):{RESET}")
        for core in self.sim.cpu.cores:
            if core.is_idle:
                core_status = f"{DIM}IDLE (waiting for ready order in buffer){RESET}"
            else:
                o = core.current_order
                bar = make_bar(core.progress_percentage, width=12, color=CYAN)
                slack_str = f"Slack: {format_time_minutes(o.slack_time)}" if o.cold_items else "Shelf-only"
                core_status = f"{BOLD}{YELLOW}[{o.order_id}]{RESET} Packing... {bar} | {slack_str}"
            lines.append(f"  ▶ Core {core.core_id}: {core_status}")
        lines.append("─" * 80)

        # 4. Buffer Table (ROB / Cache)
        chilled_tag = f"{CYAN}[Chilled Protection ON - 0.33x decay]{RESET}" if self.sim.buffer_table.chilled_protection else f"{YELLOW}[Standard Room Temp - 1.0x decay]{RESET}"
        lines.append(f"{BOLD}📥 BUFFER TABLE (Reorder Buffer) {chilled_tag}:{RESET}")

        if not self.sim.buffer_table.staging:
            lines.append(f"  {DIM}(Buffer table is currently empty){RESET}")
        else:
            for order_id, items in list(self.sim.buffer_table.staging.items())[:6]:
                order = next((o for o in self.sim.orders if o.order_id == order_id), None)
                if not order:
                    continue

                colds = [it for it in items if it.item_type == ItemType.COLD]
                shelfs = [it for it in items if it.item_type == ItemType.SHELF]

                ready_badge = f"{GREEN}[READY 100%]{RESET}" if self.sim.buffer_table.is_order_complete(order) else f"{YELLOW}[WAITING ARRV]{RESET}"

                # Show minimum freshness for cold items
                if colds:
                    min_fresh = min(it.freshness_percentage for it in colds)
                    color = GREEN if min_fresh > 50 else (YELLOW if min_fresh > 25 else RED)
                    cold_str = f"❄️ {len(colds)} cold (Freshness: {make_bar(min_fresh, 8, color)})"
                else:
                    cold_str = "❄️ 0 cold"

                shelf_str = f"📦 {len(shelfs)}/{len(order.shelf_items)} shelf"
                lines.append(f"  • {BOLD}{order_id}{RESET} {ready_badge} | {cold_str} | {shelf_str}")

            if len(self.sim.buffer_table.staging) > 6:
                lines.append(f"  {DIM}...and {len(self.sim.buffer_table.staging) - 6} more orders in buffer{RESET}")

        lines.append("─" * 80)

        # 5. Pipeline & Storage Transit Status
        in_transit_count = len(self.sim.storage_pipeline.in_transit)
        pending_orders = sum(1 for o in self.sim.orders if o.status in (OrderStatus.PENDING, OrderStatus.DISPATCHING))
        lines.append(
            f"{BOLD}🚚 INVENTORY & PIPELINE:{RESET} "
            f"Items in Transit to Buffer: {CYAN}{in_transit_count}{RESET} | "
            f"Orders in Pipeline: {YELLOW}{pending_orders}{RESET}"
        )
        lines.append("─" * 80)

        # 6. Live Event Log
        lines.append(f"{BOLD}📜 EVENT LOG (Recent Activity):{RESET}")
        recent_logs = self.sim.event_log[-5:] if self.sim.event_log else ["(No events yet)"]
        for ev in recent_logs:
            lines.append(f"  {ev}")

        return "\n".join(lines)
