"""End-to-end run: load → order check → allocation → consolidation → Excel."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .allocation import AllocationResult, solve_allocation
from .config import Settings
from .consolidation import ConsolidationResult, solve_consolidation
from .data import load_input, select_fulfillable_orders, stock_check
from .verify import check_allocation, check_consolidation


@dataclass
class PipelineResult:
    demand: pd.DataFrame
    inventory: pd.DataFrame
    stock_check: pd.DataFrame
    rejected_orders: pd.DataFrame
    allocation: AllocationResult
    consolidation: ConsolidationResult
    runtime_sec: float
    errors: list[str] = field(default_factory=list)

    def kpis(self) -> dict:
        a, c = self.allocation.summary, self.consolidation.summary
        return {
            "orders_served": int(self.demand["production_order"].nunique()),
            "orders_rejected": int(len(self.rejected_orders)),
            "pick_lines": int(a["pick_lines"].sum()),
            "bins_opened": int(a["bins_opened"].sum()),
            "bins_emptied": int(a["bins_emptied"].sum()),
            "fragments_created": int(a["fragments_created"].sum()),
            "consolidation_moves": int(c["moves"].sum()) if not c.empty else 0,
            "bins_freed_by_consolidation": int(c["bins_freed"].sum()) if not c.empty else 0,
            "fragments_remaining": int(c["fragments_remaining"].sum()) if not c.empty else 0,
            "pick_stage_not_proven_optimal": int((a["solver_status"].str.split("/").str[0] != "Optimal").sum()),
            "runtime_sec": round(self.runtime_sec, 2),
        }


def run_pipeline(demand: pd.DataFrame, inventory: pd.DataFrame, settings: Settings | None = None) -> PipelineResult:
    settings = settings or Settings()
    t0 = time.perf_counter()
    check = stock_check(demand, inventory)
    accepted, rejected = select_fulfillable_orders(demand, inventory, settings)
    alloc = solve_allocation(accepted, inventory, settings)
    cons = solve_consolidation(alloc.bin_status, settings)
    errors = check_allocation(accepted, inventory, alloc.allocation) + check_consolidation(cons.bin_status, cons.moves, settings.max_split)
    return PipelineResult(accepted, inventory, check, rejected, alloc, cons, time.perf_counter() - t0, errors)


def run_file(input_path: str | Path, output_path: str | Path | None = None,
             settings: Settings | None = None) -> PipelineResult:
    settings = settings or Settings()
    demand, inventory = load_input(input_path, settings)
    result = run_pipeline(demand, inventory, settings)
    if output_path:
        export_excel(result, output_path)
    return result


def export_excel(result: PipelineResult, path: str | Path) -> None:
    kpi = pd.DataFrame(list(result.kpis().items()), columns=["kpi", "value"])
    alloc_view = result.allocation.allocation[["bin_no", "item_code", "production_order", "amount_allocated"]] \
        .sort_values(["bin_no", "item_code", "production_order"])
    touched = result.consolidation.bin_status
    touched = touched[(touched["allocated"] > 0) | (touched["final_quantity"] != touched["left_over"])]
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        kpi.to_excel(writer, sheet_name="summary", index=False)
        alloc_view.to_excel(writer, sheet_name="bin_allocation_decision", index=False)
        result.consolidation.moves.to_excel(writer, sheet_name="bin_consolidation_decision", index=False)
        touched.to_excel(writer, sheet_name="bin_status", index=False)
        result.allocation.summary.to_excel(writer, sheet_name="allocation_by_item", index=False)
        result.consolidation.summary.to_excel(writer, sheet_name="consolidation_by_item", index=False)
        result.stock_check.to_excel(writer, sheet_name="stock_check", index=False)
        result.rejected_orders.to_excel(writer, sheet_name="rejected_orders", index=False)
