"""Tests for binopt (PuLP + HiGHS). Run: python -m pytest -q"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from binopt import (Settings, export_excel, load_input, prepare_demand, prepare_inventory, run_pipeline,
                    select_fulfillable_orders, solve_allocation, solve_consolidation)
from binopt.allocation import build_bin_status
from binopt.verify import check_allocation, check_consolidation

ROOT = Path(__file__).resolve().parents[1]
FAST = Settings(time_limit=30, tiebreak_time_limit=5)
REAL = Settings(tiebreak_time_limit=5)   # default 60 s for the pick-line stage


def demand(*rows):
    """rows: (production_order, item_code, qty[, priority])"""
    df = pd.DataFrame(rows, columns=["production_order", "item_code", "material_requirements", "priority"][:len(rows[0])])
    return prepare_demand(df)


def inventory(*rows, settings=FAST):
    """rows: (bin_no, item_code, stock[, capacity])"""
    cols = ["bin_no", "item_code", "stock_on_hand", "bin_capacity"][:len(rows[0])]
    return prepare_inventory(pd.DataFrame(rows, columns=cols), settings)


def bin_status(*rows, threshold=1000):
    """rows: (bin_no, stock, allocated[, capacity]) of item 'A' -> bin_status as produced by allocation."""
    inv = inventory(*[(r[0], "A", r[1], *r[3:]) for r in rows])
    alloc = pd.DataFrame([("P", "A", r[0], r[2]) for r in rows if r[2] > 0],
                         columns=["production_order", "item_code", "bin_no", "amount_allocated"])
    return build_bin_status(inv, alloc, ["A"], threshold)


# ----------------------------------------------------------------- allocation
def test_minimum_pick_lines():
    d = demand(("P1", "A", 5000))
    inv = inventory((1, "A", 3000), (2, "A", 2000), (3, "A", 2500))
    res = solve_allocation(d, inv, FAST)
    assert len(res.allocation) == 2
    assert res.allocation["amount_allocated"].sum() == 5000
    assert check_allocation(d, inv, res.allocation) == []


def test_fragment_avoidance_prefers_bin_that_stays_above_threshold():
    d = demand(("P1", "A", 2000))
    inv = inventory((1, "A", 3000), (2, "A", 2900))
    res = solve_allocation(d, inv, FAST)
    assert res.allocation["bin_no"].tolist() == [1]            # leaves 1000 -> not a fragment
    assert res.summary["fragments_created"].iloc[0] == 0


def test_without_fragment_avoidance_still_minimises_picks():
    d = demand(("P1", "A", 2000))
    inv = inventory((1, "A", 3000), (2, "A", 2900))
    res = solve_allocation(d, inv, Settings(avoid_fragments_in_allocation=False, tiebreak_time_limit=5))
    assert len(res.allocation) == 1


def test_shared_bin_left_over_is_aggregated_per_bin():
    """Regression: the notebook computed left-over per allocation row, not per bin."""
    d = demand(("P1", "A", 1500), ("P2", "A", 1500))
    inv = inventory((1, "A", 3000), (2, "A", 2000), (3, "A", 2000))
    res = solve_allocation(d, inv, FAST)
    assert len(res.allocation) == 2
    bs = res.bin_status.set_index("bin_no")
    assert bs.loc[1, "allocated"] == 3000 and bs.loc[1, "left_over"] == 0
    assert bs.loc[1, "status"] == "emptied"
    assert (bs.loc[[2, 3], "status"] == "untouched").all()


def test_allocation_is_integer_and_per_item():
    d = demand(("P1", "A", 1234), ("P1", "B", 777), ("P2", "B", 1500))
    inv = inventory((1, "A", 3000), (2, "B", 1000), (3, "B", 2500), (4, "C", 3000))
    res = solve_allocation(d, inv, FAST)
    assert res.allocation["amount_allocated"].dtype.kind == "i"
    assert set(res.bin_status["item_code"]) == {"A", "B"}
    assert check_allocation(d, inv, res.allocation) == []


def test_allocation_raises_when_stock_is_short():
    with pytest.raises(ValueError, match="exceeds stock"):
        solve_allocation(demand(("P1", "A", 5000)), inventory((1, "A", 3000)), FAST)


# ------------------------------------------------------------ order selection
def test_order_selection_keeps_everything_when_stock_is_enough():
    d = demand(("P1", "A", 1000), ("P2", "A", 1000))
    acc, rej = select_fulfillable_orders(d, inventory((1, "A", 3000)), FAST)
    assert len(acc) == 2 and rej.empty


def test_order_selection_respects_priority_then_quantity():
    d = demand(("P1", "A", 2000, 1), ("P2", "A", 2500, 5), ("P3", "B", 500, 1))
    inv = inventory((1, "A", 3000), (2, "B", 1000))
    acc, rej = select_fulfillable_orders(d, inv, FAST)
    assert set(acc["production_order"]) == {"P2", "P3"}
    assert rej["production_order"].tolist() == ["P1"]


def test_order_selection_is_all_or_nothing_per_order():
    # P1 needs A and B; B is short, so P1 is dropped entirely and P2 is served.
    d = demand(("P1", "A", 1000), ("P1", "B", 2000), ("P2", "B", 1500))
    inv = inventory((1, "A", 3000), (2, "B", 1800))
    acc, rej = select_fulfillable_orders(d, inv, FAST)
    assert acc["production_order"].unique().tolist() == ["P2"]
    assert rej["production_order"].tolist() == ["P1"]


# -------------------------------------------------------------- consolidation
def test_fragments_merge_into_one_bin_moving_least_quantity():
    bs = bin_status((1, 3000, 2500), (2, 3000, 2600), (3, 3000, 2700))   # left 500 / 400 / 300
    res = solve_consolidation(bs, FAST)
    assert len(res.moves) == 2
    assert set(res.moves["destination_bin"]) == {1}                     # the largest stays
    assert res.moves["quantity_moved"].sum() == 700
    assert res.summary["fragments_remaining"].iloc[0] == 0              # 1200 >= threshold
    final = res.bin_status.set_index("bin_no")["final_quantity"]
    assert final.tolist() == [1200, 0, 0]
    assert check_consolidation(res.bin_status, res.moves) == []


def test_fragment_tops_up_partial_bin():
    bs = bin_status((1, 3000, 2400), (2, 3000, 1000))                    # fragment 600, partial 2000
    res = solve_consolidation(bs, FAST)
    assert res.moves[["origin_bin", "destination_bin", "quantity_moved"]].values.tolist() == [[1, 2, 600]]
    assert res.bin_status.set_index("bin_no").loc[2, "final_quantity"] == 2600


def test_fragments_only_receivers_reproduces_original_scope():
    bs = bin_status((1, 3000, 2400), (2, 3000, 1000))
    res = solve_consolidation(bs, Settings(allow_full_bin_receivers=False, tiebreak_time_limit=5))
    assert res.moves.empty


def test_emptied_bins_are_never_used():
    bs = bin_status((1, 3000, 3000), (2, 3000, 2500), (3, 3000, 2800))   # emptied, 500, 200
    res = solve_consolidation(bs, FAST)
    assert 1 not in set(res.moves["origin_bin"]) | set(res.moves["destination_bin"])
    assert len(res.moves) == 1 and res.moves["quantity_moved"].iloc[0] == 200


def test_capacity_and_no_chain():
    # three fragments of 900 in bins of capacity 2000: only one bin can be freed (2700 > 2000).
    # Whole-bin: 1800 + 900 (one fragment left). Split 450/450: 1350 + 1350 (no fragment left).
    bs = bin_status((1, 2000, 1100, 2000), (2, 2000, 1100, 2000), (3, 2000, 1100, 2000))
    res = solve_consolidation(bs, FAST)
    assert res.summary["bins_freed"].iloc[0] == 1
    assert res.summary["fragments_remaining"].iloc[0] == 0
    assert len(res.moves) == 2 and res.moves["origin_bin"].nunique() == 1
    whole = solve_consolidation(bs, Settings(max_split=1, time_limit=30, tiebreak_time_limit=5))
    assert len(whole.moves) == 1 and whole.summary["fragments_remaining"].iloc[0] == 1
    assert (res.bin_status["final_quantity"] <= 2000).all()
    assert check_consolidation(res.bin_status, res.moves) == []


def test_fragment_split_over_two_partial_bins():
    # fragment 800 fits in no single bin (room 400 + 400) -> split in 2 parts frees it
    bs = bin_status((1, 3000, 2200), (2, 3000, 400), (3, 3000, 400))
    res = solve_consolidation(bs, FAST)
    assert sorted(res.moves[["destination_bin", "quantity_moved"]].values.tolist()) == [[2, 400], [3, 400]]
    assert res.bin_status.set_index("bin_no")["final_quantity"].tolist() == [0, 3000, 3000]
    assert res.summary["split_fragments"].iloc[0] == 1
    assert check_consolidation(res.bin_status, res.moves, max_split=2) == []


def test_split_is_limited_to_two_parts():
    # fragment 900 would need 3 receivers of 300 room each -> not allowed
    bs = bin_status((1, 3000, 2100), (2, 3000, 300), (3, 3000, 300), (4, 3000, 300))
    assert solve_consolidation(bs, FAST).moves.empty


def test_max_split_one_means_whole_bin_moves():
    bs = bin_status((1, 3000, 2200), (2, 3000, 400), (3, 3000, 400))
    res = solve_consolidation(bs, Settings(max_split=1, time_limit=30, tiebreak_time_limit=5))
    assert res.moves.empty


def test_split_used_only_when_it_pays_off():
    # 600 fits whole into bin 2 -> one move, not two
    bs = bin_status((1, 3000, 2400), (2, 3000, 1000), (3, 3000, 500))
    res = solve_consolidation(bs, FAST)
    assert len(res.moves) == 1


def test_validators_catch_bad_plans():
    bs = bin_status((1, 3000, 2500), (2, 3000, 2600), (3, 3000, 2700))
    bs["final_quantity"] = bs["left_over"]
    chain = pd.DataFrame([("A", 3, 2, 300, 0, 0, 3000), ("A", 2, 1, 400, 0, 0, 3000)],
                         columns=["item_code", "origin_bin", "destination_bin", "quantity_moved",
                                  "destination_before", "destination_after", "destination_capacity"])
    assert any("both moved and receiving" in e for e in check_consolidation(bs, chain))
    bs4 = bin_status((1, 3000, 2700), (2, 3000, 0), (3, 3000, 0), (4, 3000, 0))
    bs4["final_quantity"] = bs4["left_over"]
    split3 = pd.DataFrame([("A", 1, n, 100, 0, 0, 3000) for n in (2, 3, 4)], columns=chain.columns)
    assert any("split over more than 2" in e for e in check_consolidation(bs4, split3, max_split=2))

    d = demand(("P1", "A", 100))
    inv = inventory((1, "A", 50))
    bad = pd.DataFrame([("P1", "A", 1, 100)], columns=["production_order", "item_code", "bin_no", "amount_allocated"])
    assert any("stock exceeded" in e for e in check_allocation(d, inv, bad))


# ------------------------------------------------------------- input checks
def test_input_validation():
    with pytest.raises(ValueError, match="duplicated bin_no"):
        inventory((1, "A", 10), (1, "B", 10))
    with pytest.raises(ValueError, match="exceeds bin capacity"):
        inventory((1, "A", 3500))
    with pytest.raises(ValueError, match="whole numbers"):
        demand(("P1", "A", 10.5))
    assert len(demand(("P1", "A", 10), ("P1", "A", 5), ("P2", "A", 0))) == 1  # merged, zero dropped


# ------------------------------------------------------------ real data set
@pytest.fixture(scope="module")
def real_run():
    d, inv = load_input(ROOT / "input_template.xlsx", REAL)
    return run_pipeline(d, inv, REAL)


def test_real_data_end_to_end(real_run):
    assert real_run.errors == []
    k = real_run.kpis()
    assert k["orders_rejected"] == 0
    assert k["pick_stage_not_proven_optimal"] == 0
    assert k["pick_lines"] == 151            # same optimum as Gurobi on the original model
    assert k["fragments_remaining"] <= 3
    assert (real_run.allocation.summary["demand"] == real_run.allocation.summary["allocated"]).all()


def test_excel_export(real_run, tmp_path):
    out = tmp_path / "out.xlsx"
    export_excel(real_run, out)
    sheets = pd.read_excel(out, sheet_name=None)
    assert {"summary", "bin_allocation_decision", "bin_combination_decision", "bin_status"} <= set(sheets)
    assert len(sheets["bin_allocation_decision"]) == real_run.kpis()["pick_lines"]
    assert (sheets["bin_combination_decision"]["quantity_moved"] > 0).all()   # no 0-quantity rows
