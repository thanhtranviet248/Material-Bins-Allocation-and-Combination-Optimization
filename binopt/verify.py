"""Independent feasibility checks on solver output (used by tests and the pipeline)."""
from __future__ import annotations

import pandas as pd


def check_allocation(demand: pd.DataFrame, inventory: pd.DataFrame, allocation: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    if (allocation["amount_allocated"] <= 0).any():
        errors.append("non-positive allocation rows")
    item_of = inventory.set_index("bin_no")["item_code"]
    wrong = allocation[allocation["bin_no"].map(item_of) != allocation["item_code"]]
    if not wrong.empty:
        errors.append(f"bins used for a different item: {wrong['bin_no'].tolist()[:10]}")

    got = allocation.groupby(["production_order", "item_code"])["amount_allocated"].sum()
    need = demand.set_index(["production_order", "item_code"])["material_requirements"]
    diff = need.subtract(got, fill_value=0)
    if (diff != 0).any():
        errors.append(f"demand not met exactly: {diff[diff != 0].to_dict()}")

    used = allocation.groupby("bin_no")["amount_allocated"].sum()
    stock = inventory.set_index("bin_no")["stock_on_hand"]
    over = used[used > stock.reindex(used.index).fillna(0)]
    if not over.empty:
        errors.append(f"stock exceeded in bins: {over.index.tolist()[:10]}")
    return errors


def check_consolidation(bin_status: pd.DataFrame, moves: pd.DataFrame, max_split: int = 2) -> list[str]:
    errors: list[str] = []
    if moves.empty:
        return errors
    info = bin_status.set_index("bin_no")
    if (moves["quantity_moved"] <= 0).any():
        errors.append("non-positive move quantity")
    if moves.duplicated(["origin_bin", "destination_bin"]).any():
        errors.append("duplicated origin/destination pair")
    if (moves["origin_bin"] == moves["destination_bin"]).any():
        errors.append("bin moved into itself")
    if (moves.groupby("origin_bin").size() > max_split).any():
        errors.append(f"a fragment is split over more than {max_split} bins")
    chain = set(moves["origin_bin"]) & set(moves["destination_bin"])
    if chain:
        errors.append(f"bins both moved and receiving: {sorted(chain)[:10]}")
    same_item = moves["item_code"].eq(moves["origin_bin"].map(info["item_code"])) & \
        moves["item_code"].eq(moves["destination_bin"].map(info["item_code"]))
    if not same_item.all():
        errors.append("move between bins of different items")
    out = moves.groupby("origin_bin")["quantity_moved"].sum()
    if not out.eq(info["left_over"].reindex(out.index)).all():
        errors.append("origin bin not emptied completely")
    level = info["left_over"].copy()
    for m in moves.itertuples():
        level[m.destination_bin] += m.quantity_moved
        level[m.origin_bin] -= m.quantity_moved
    over = level[level > info["bin_capacity"]]
    if not over.empty:
        errors.append(f"capacity exceeded in bins: {over.index.tolist()[:10]}")
    if "final_quantity" in info and not level.eq(info["final_quantity"]).all():
        errors.append("final_quantity inconsistent with moves")
    if bin_status["left_over"].sum() != bin_status["final_quantity"].sum():
        errors.append("stock not conserved")
    return errors
