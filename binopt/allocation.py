"""Problem 1 – allocate bins to production orders.

Because a bin holds exactly one item, the problem separates by item: one small
MILP per item instead of one dense model over every (order, item, bin) triple.

For item j with orders I_j (demand d_i) and bins K_j (stock s_k), threshold T:

    x_ik ∈ Z+    quantity taken from bin k for order i, x_ik ≤ min(s_k, d_i)·y_ik
    y_ik ∈ {0,1} bin k is picked for order i            (one pick line)
    z_k  ∈ {0,1} bin k is touched by at least one order  (y_ik ≤ z_k)
    e_k  ∈ {0,1} bin k is emptied                        (r_k ≤ s_k·(1−e_k))
    f_k  ∈ {0,1} bin k becomes a fragment                (r_k ≥ T·(z_k − e_k − f_k))
    r_k  = s_k − Σ_i x_ik ≥ 0                            left-over of bin k

    Σ_k x_ik = d_i                                       demand met exactly

Lexicographic objectives:
    1. min Σ y_ik   – pick lines (the original objective)
    2. min Σ f_k    – fragments created (bins touched, not emptied, left-over < T)
    3. min Σ z_k    – distinct bins opened
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pulp

from .config import Settings
from .solver import Stage, new_var, int_value, solve_lexicographic


@dataclass
class AllocationResult:
    allocation: pd.DataFrame
    """production_order, item_code, bin_no, amount_allocated"""
    bin_status: pd.DataFrame
    """every bin of a demanded item: stock, allocated, left_over, status"""
    summary: pd.DataFrame
    """one row per item with KPI and solver status"""


def _solve_item(item: str, demand: dict, bins: dict, settings: Settings):
    T = settings.fragment_threshold
    orders = list(demand)
    bin_ids = [k for k, s in bins.items() if s > 0]
    prob = pulp.LpProblem(f"allocation_{item}", pulp.LpMinimize)

    x, y = {}, {}
    for a, i in enumerate(orders):
        for b, k in enumerate(bin_ids):
            ub = min(bins[k], demand[i])
            x[i, k] = new_var(prob, f"x_{a}_{b}", 0, ub, cat="Integer")
            y[i, k] = new_var(prob, f"y_{a}_{b}", cat="Binary")
            prob += x[i, k] <= ub * y[i, k], f"link_{a}_{b}"
    z = {k: new_var(prob, f"z_{b}", cat="Binary") for b, k in enumerate(bin_ids)}
    e = {k: new_var(prob, f"e_{b}", cat="Binary") for b, k in enumerate(bin_ids)}
    f = {k: new_var(prob, f"f_{b}", cat="Binary") for b, k in enumerate(bin_ids)}

    for a, i in enumerate(orders):
        prob += pulp.lpSum(x[i, k] for k in bin_ids) == demand[i], f"demand_{a}"
    for b, k in enumerate(bin_ids):
        taken = pulp.lpSum(x[i, k] for i in orders)
        left = bins[k] - taken
        prob += taken <= bins[k], f"supply_{b}"
        for a, i in enumerate(orders):
            prob += y[i, k] <= z[k], f"touch_{a}_{b}"
        prob += left <= bins[k] * (1 - e[k]), f"empty_{b}"
        prob += left >= T * (z[k] - e[k] - f[k]), f"fragment_{b}"
        prob += e[k] <= z[k], f"empty_touch_{b}"
        prob += f[k] <= z[k], f"frag_touch_{b}"

    stages = [Stage("picks", pulp.lpSum(y.values()))]
    if settings.avoid_fragments_in_allocation:
        stages.append(Stage("fragments", pulp.lpSum(f.values())))
    stages.append(Stage("bins_opened", pulp.lpSum(z.values())))
    results = solve_lexicographic(prob, stages, settings)

    rows = [
        {"production_order": i, "item_code": item, "bin_no": k, "amount_allocated": int_value(x[i, k])}
        for i in orders for k in bin_ids if int_value(x[i, k]) > 0
    ]
    status = "/".join(r.status for r in results)
    return rows, status


def classify_bin(stock: int, allocated: int, left: int, threshold: int) -> str:
    if allocated == 0:
        return "untouched"
    if left == 0:
        return "emptied"
    return "fragment" if left < threshold else "partial"


def build_bin_status(inventory: pd.DataFrame, allocation: pd.DataFrame, items, threshold: int) -> pd.DataFrame:
    inv = inventory[inventory["item_code"].isin(items)].copy()
    taken = allocation.groupby("bin_no")["amount_allocated"].sum() if not allocation.empty else pd.Series(dtype=int)
    inv["allocated"] = inv["bin_no"].map(taken).fillna(0).astype(int)
    inv["left_over"] = inv["stock_on_hand"] - inv["allocated"]
    inv["status"] = [
        classify_bin(s, a, r, threshold)
        for s, a, r in zip(inv["stock_on_hand"], inv["allocated"], inv["left_over"])
    ]
    return inv.sort_values(["item_code", "bin_no"]).reset_index(drop=True)


def solve_allocation(demand: pd.DataFrame, inventory: pd.DataFrame, settings: Settings | None = None) -> AllocationResult:
    settings = settings or Settings()
    rows, summary = [], []
    items = demand["item_code"].unique().tolist()
    for item in items:
        d_item = demand[demand["item_code"] == item].set_index("production_order")["material_requirements"].to_dict()
        b_item = inventory[inventory["item_code"] == item].set_index("bin_no")["stock_on_hand"].to_dict()
        if sum(b_item.values()) < sum(d_item.values()):
            raise ValueError(f"Item {item}: demand {sum(d_item.values())} exceeds stock {sum(b_item.values())}. "
                             "Run select_fulfillable_orders first.")
        item_rows, status = _solve_item(item, d_item, b_item, settings)
        rows += item_rows
        summary.append({"item_code": item, "solver_status": status})

    allocation = pd.DataFrame(rows, columns=["production_order", "item_code", "bin_no", "amount_allocated"])
    allocation = allocation.sort_values(["item_code", "bin_no", "production_order"]).reset_index(drop=True)
    bin_status = build_bin_status(inventory, allocation, items, settings.fragment_threshold)

    kpi = bin_status.groupby("item_code").agg(
        bins_available=("bin_no", "count"),
        bins_opened=("allocated", lambda s: int((s > 0).sum())),
        bins_emptied=("status", lambda s: int((s == "emptied").sum())),
        fragments_created=("status", lambda s: int((s == "fragment").sum())),
    )
    kpi["pick_lines"] = allocation.groupby("item_code").size()
    kpi["demand"] = demand.groupby("item_code")["material_requirements"].sum()
    kpi["allocated"] = allocation.groupby("item_code")["amount_allocated"].sum()
    summary_df = kpi.reset_index().merge(pd.DataFrame(summary), on="item_code")
    cols = ["item_code", "demand", "allocated", "pick_lines", "bins_opened", "bins_emptied",
            "fragments_created", "bins_available", "solver_status"]
    return AllocationResult(allocation, bin_status, summary_df[cols])
