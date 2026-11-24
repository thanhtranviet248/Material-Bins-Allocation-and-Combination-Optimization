"""Input loading, validation and the order-fulfilment check."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pulp

from .config import Settings
from .solver import Stage, new_var, solve_lexicographic

DEMAND_COLS = ["production_order", "item_code", "material_requirements"]
INVENTORY_COLS = ["bin_no", "item_code", "stock_on_hand"]


def _require(df: pd.DataFrame, cols: list[str], sheet: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Sheet '{sheet}' is missing columns: {missing}")


def _as_int(series: pd.Series, name: str) -> pd.Series:
    if series.isna().any():
        raise ValueError(f"Column '{name}' contains empty values.")
    if not (series == series.round()).all():
        raise ValueError(f"Column '{name}' must contain whole numbers.")
    if (series < 0).any():
        raise ValueError(f"Column '{name}' must be non-negative.")
    return series.astype(int)


def prepare_demand(demand: pd.DataFrame) -> pd.DataFrame:
    """Clean the demand sheet. Any ``stock_check`` column is ignored (recomputed by the model)."""
    _require(demand, DEMAND_COLS, "production_order")
    df = demand.copy()
    df["production_order"] = df["production_order"].astype(str).str.strip()
    df["item_code"] = df["item_code"].astype(str).str.strip()
    df["material_requirements"] = _as_int(df["material_requirements"], "material_requirements")
    if "priority" in df.columns:
        df["priority"] = pd.to_numeric(df["priority"]).fillna(1.0)
    else:
        df["priority"] = 1.0
    df = (
        df.groupby(["production_order", "item_code"], as_index=False, sort=False)
        .agg(material_requirements=("material_requirements", "sum"), priority=("priority", "max"))
    )
    return df[df["material_requirements"] > 0].reset_index(drop=True)


def prepare_inventory(inventory: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    _require(inventory, INVENTORY_COLS, "inventory")
    df = inventory.copy()
    if df["bin_no"].duplicated().any():
        dups = df.loc[df["bin_no"].duplicated(), "bin_no"].unique().tolist()[:10]
        raise ValueError(f"Each bin can hold one item only; duplicated bin_no: {dups}")
    df["item_code"] = df["item_code"].astype(str).str.strip()
    df["stock_on_hand"] = _as_int(df["stock_on_hand"], "stock_on_hand")
    if "bin_capacity" in df.columns:
        df["bin_capacity"] = _as_int(df["bin_capacity"].fillna(settings.default_capacity), "bin_capacity")
    else:
        df["bin_capacity"] = settings.default_capacity
    over = df[df["stock_on_hand"] > df["bin_capacity"]]
    if not over.empty:
        raise ValueError(f"Stock exceeds bin capacity for bins: {over['bin_no'].tolist()[:10]}")
    return df[["bin_no", "item_code", "stock_on_hand", "bin_capacity"]].reset_index(drop=True)


def load_input(path: str | Path, settings: Settings | None = None,
               demand_sheet: str = "production_order", inventory_sheet: str = "inventory"):
    settings = settings or Settings()
    demand = pd.read_excel(path, sheet_name=demand_sheet)
    inventory = pd.read_excel(path, sheet_name=inventory_sheet)
    return prepare_demand(demand), prepare_inventory(inventory, settings)


def stock_check(demand: pd.DataFrame, inventory: pd.DataFrame) -> pd.DataFrame:
    """Aggregate demand vs stock per item (replaces the XLOOKUP/pivot in the Excel file)."""
    d = demand.groupby("item_code")["material_requirements"].sum()
    s = inventory.groupby("item_code")["stock_on_hand"].sum()
    out = pd.DataFrame({"demand": d}).join(pd.DataFrame({"stock": s}), how="left").fillna(0).astype(int)
    out["enough_stock"] = out["stock"] >= out["demand"]
    return out.reset_index()


def select_fulfillable_orders(demand: pd.DataFrame, inventory: pd.DataFrame, settings: Settings):
    """Choose which production orders can be served completely.

    If every item has enough total stock, all orders are kept. Otherwise a small
    MILP picks the orders (all-or-nothing) that maximise total priority, then
    total quantity served. Aggregate stock is a sufficient condition because a
    bin may be split between orders, so the allocation stage stays feasible.

    Returns ``(accepted_demand, rejected_orders_df)``.
    """
    check = stock_check(demand, inventory)
    if check["enough_stock"].all():
        return demand.copy(), pd.DataFrame(columns=["production_order", "reason"])

    stock = inventory.groupby("item_code")["stock_on_hand"].sum().to_dict()
    orders = demand["production_order"].unique().tolist()
    prio = demand.groupby("production_order")["priority"].max().to_dict()
    qty = demand.groupby("production_order")["material_requirements"].sum().to_dict()

    prob = pulp.LpProblem("order_selection", pulp.LpMaximize)
    u = {o: new_var(prob, f"u_{n}", cat="Binary") for n, o in enumerate(orders)}
    for item, grp in demand.groupby("item_code"):
        prob += (
            pulp.lpSum(r.material_requirements * u[r.production_order] for r in grp.itertuples())
            <= stock.get(item, 0),
            f"stock_{item}",
        )
    solve_lexicographic(
        prob,
        [
            Stage("priority", pulp.lpSum(prio[o] * u[o] for o in orders), pulp.LpMaximize, integral=False),
            Stage("quantity", pulp.lpSum(qty[o] * u[o] for o in orders), pulp.LpMaximize),
        ],
        settings,
    )
    accepted = [o for o in orders if round(u[o].value() or 0) == 1]
    rejected = pd.DataFrame(
        {"production_order": [o for o in orders if o not in accepted], "reason": "insufficient total stock"}
    )
    return demand[demand["production_order"].isin(accepted)].reset_index(drop=True), rejected
