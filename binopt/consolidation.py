"""Problem 2 – consolidate fragment bins after allocation.

Per item, with left-over r_k and capacity C_k after allocation:

    O  = fragment bins (touched, 0 < r_k < T)            – may be emptied
    R  = bins that can receive (r_k > 0, spare room)      – fragments, and
         optionally partial/untouched bins of the same item
    w_m  ∈ {0,1}  fragment m is emptied completely
    a_mn ∈ {0,1}  route m → n is used (one forklift move)
    q_mn ∈ Z+     quantity moved on route m → n
    g_n  ∈ {0,1}  fragment n stays and is still below T

    Σ_n q_mn = r_m·w_m                 a fragment is emptied completely or not touched
    Σ_n a_mn ≤ S·w_m                   ... split over at most S receivers (default S = 2)
    a_mn ≤ q_mn ≤ min(r_m, C_n − r_n)·a_mn
    r_n + Σ_m q_mn ≤ C_n               destination capacity (its own stock included)
    a_mn + w_n ≤ 1          (n ∈ O)    no chains: a bin that is emptied cannot receive
    r_n + Σ_m q_mn ≥ T·(1 − w_n − g_n) marks fragments that remain small

Lexicographic objectives:
    1. max Σ w_m              – bins freed (= min bins occupied)
    2. min Σ g_n              – fragments still below the threshold
    3. min Σ a_mn             – forklift moves (splits only when they pay off)
    4. min Σ r_m·w_m          – quantity handled
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pulp

from .config import Settings
from .solver import Stage, new_var, int_value, solve_lexicographic

MOVE_COLS = ["item_code", "origin_bin", "destination_bin", "quantity_moved",
             "destination_before", "destination_after", "destination_capacity"]


@dataclass
class ConsolidationResult:
    moves: pd.DataFrame
    bin_status: pd.DataFrame
    """bin_status from allocation plus final_quantity / final_status"""
    summary: pd.DataFrame


def _origins_and_receivers(df: pd.DataFrame, settings: Settings):
    T = settings.fragment_threshold
    small = (df["left_over"] > 0) & (df["left_over"] < T)
    origin_mask = small & ((df["status"] == "fragment") | settings.include_untouched_fragments)
    origins = df[origin_mask]
    if settings.allow_full_bin_receivers:
        receivers = df[df["left_over"] > 0]
    else:
        receivers = origins
    return origins, receivers


def _solve_item(item: str, df: pd.DataFrame, settings: Settings):
    """Return ``(moves, status)`` with moves = [(origin, destination, quantity)]."""
    T, S = settings.fragment_threshold, max(1, int(settings.max_split))
    origins, receivers = _origins_and_receivers(df, settings)
    r = df.set_index("bin_no")["left_over"].to_dict()
    cap = df.set_index("bin_no")["bin_capacity"].to_dict()
    O = origins["bin_no"].tolist()
    R = receivers["bin_no"].tolist()
    if len(O) == 0:
        return [], "no fragments"

    ub = {(m, n): min(r[m], cap[n] - r[n]) for m in O for n in R if m != n}
    pairs = [p for p, u in ub.items() if u > 0]
    # an origin can only be emptied if its S largest routes can hold it
    room = {m: sorted((ub[p] for p in pairs if p[0] == m), reverse=True)[:S] for m in O}
    O = [m for m in O if sum(room[m]) >= r[m]]
    pairs = [p for p in pairs if p[0] in set(O)]
    if not pairs:
        return [], "no feasible move"

    prob = pulp.LpProblem(f"consolidation_{item}", pulp.LpMinimize)
    idx = {k: n for n, k in enumerate(dict.fromkeys(O + R))}
    a = {(m, n): new_var(prob, f"a_{idx[m]}_{idx[n]}", cat="Binary") for m, n in pairs}
    q = {(m, n): new_var(prob, f"q_{idx[m]}_{idx[n]}", 0, ub[m, n], cat="Integer") for m, n in pairs}
    w = {m: new_var(prob, f"w_{idx[m]}", cat="Binary") for m in O}
    g = {n: new_var(prob, f"g_{idx[n]}", cat="Binary") for n in O}
    into = {n: pulp.lpSum(q[m, nn] for (m, nn) in pairs if nn == n) for n in R}

    for m in O:
        prob += pulp.lpSum(q[m, n] for (mm, n) in pairs if mm == m) == r[m] * w[m], f"empty_{idx[m]}"
        prob += pulp.lpSum(a[m, n] for (mm, n) in pairs if mm == m) <= S * w[m], f"split_{idx[m]}"
    for (m, n) in pairs:
        prob += q[m, n] <= ub[m, n] * a[m, n], f"route_ub_{idx[m]}_{idx[n]}"
        prob += q[m, n] >= a[m, n], f"route_lb_{idx[m]}_{idx[n]}"
    for n in R:
        prob += r[n] + into[n] <= cap[n], f"cap_{idx[n]}"
    O_set = set(O)
    for (m, n) in pairs:
        if n in O_set:
            prob += a[m, n] + w[n] <= 1, f"nochain_{idx[m]}_{idx[n]}"
    for n in O:
        prob += r[n] + into[n] >= T * (1 - w[n] - g[n]), f"small_{idx[n]}"

    results = solve_lexicographic(
        prob,
        [
            Stage("bins_freed", pulp.lpSum(w.values()), pulp.LpMaximize),
            Stage("fragments_remaining", pulp.lpSum(g.values())),
            Stage("moves", pulp.lpSum(a.values())),
            Stage("quantity_moved", pulp.lpSum(r[m] * w[m] for m in O)),
        ],
        settings,
    )
    moves = [(m, n, int_value(q[m, n])) for (m, n) in pairs if int_value(q[m, n]) > 0]
    return moves, "/".join(x.status for x in results)


def solve_consolidation(bin_status: pd.DataFrame, settings: Settings | None = None) -> ConsolidationResult:
    settings = settings or Settings()
    T = settings.fragment_threshold
    status = bin_status.copy()
    status["final_quantity"] = status["left_over"]
    move_rows, summary = [], []

    for item, df in status.groupby("item_code", sort=False):
        moves, solver_status = _solve_item(item, df, settings)
        r = df.set_index("bin_no")["left_over"].to_dict()
        cap = df.set_index("bin_no")["bin_capacity"].to_dict()
        level = dict(r)
        # destinations never move (no-chain constraint), so order of application is irrelevant
        for m, n, qty in sorted(moves, key=lambda p: (p[1], p[0])):
            before = level[n]
            level[n] += qty
            level[m] -= qty
            move_rows.append({"item_code": item, "origin_bin": m, "destination_bin": n,
                              "quantity_moved": qty, "destination_before": before,
                              "destination_after": level[n], "destination_capacity": cap[n]})
        status.loc[df.index, "final_quantity"] = df["bin_no"].map(level).values
        origins, _ = _origins_and_receivers(df, settings)
        moved = {m for m, _, _ in moves}
        final_small = [k for k in origins["bin_no"] if k not in moved and 0 < level[k] < T]
        summary.append({
            "item_code": item,
            "fragments_before": len(origins),
            "bins_freed": len(moved),
            "moves": len(moves),
            "split_fragments": sum(1 for m in moved if sum(1 for mm, _, _ in moves if mm == m) > 1),
            "quantity_moved": sum(r[m] for m in moved),
            "fragments_remaining": len(final_small),
            "occupied_bins_before": int((df["left_over"] > 0).sum()),
            "occupied_bins_after": sum(1 for v in level.values() if v > 0),
            "solver_status": solver_status,
        })

    def final_status(row):
        if row["final_quantity"] == 0:
            return "freed" if row["left_over"] > 0 else "empty"
        if row["final_quantity"] != row["left_over"]:
            return "received"
        return row["status"]

    status["final_status"] = status.apply(final_status, axis=1)
    moves_df = pd.DataFrame(move_rows, columns=MOVE_COLS)
    return ConsolidationResult(moves_df, status, pd.DataFrame(summary))
