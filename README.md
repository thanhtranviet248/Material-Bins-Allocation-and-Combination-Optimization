# Material Bins Allocation and Combination Optimization

This project assigns warehouse bins to production orders and then consolidates the small left-overs, for a Vietnamese manufacturer. It uses open-source tools only: **PuLP + HiGHS** (MIT licence), so no Gurobi licence is needed.

## Problem statement
- The factory receives multiple production orders (PO1, PO2, ...). Each one needs given quantities of several materials (items).
- Materials are stored in bins with a capacity of 3,000 units. A bin holds one item only, and one item can be spread over many bins.
- **Problem 1, allocation:** assign bins and quantities to production orders so that every requirement is met without exceeding stock, using as few pick lines as possible.
- **Problem 2, consolidation:** a bin that is touched by allocation and keeps a left-over of less than 1,000 units is a *fragment*. Move fragments into other bins of the same item to reduce the number of occupied bins, without exceeding bin capacity.
  - A fragment can be moved into any bin of the same item that is untouched, partially picked or another fragment, as long as capacity is respected.
  - A moved fragment is emptied completely, but its content may be split over **at most 2** receiving bins, to keep operations simple (`--max-split`).

## Quick start
```bash
pip install -r requirements.txt
python -m binopt input_template.xlsx -o output_template.xlsx      # CLI
python -m pytest -q                                              # 22 tests
```
Python API, also used by `bin_allocation_and_combination_optimization.ipynb`:
```python
from binopt import Settings, load_input, run_pipeline, export_excel
settings = Settings(default_capacity=3000, fragment_threshold=1000, solver="highs")
demand, inventory = load_input("input_template.xlsx", settings)
result = run_pipeline(demand, inventory, settings)
print(result.kpis()); export_excel(result, "output.xlsx")
```
CLI options: `--capacity`, `--threshold`, `--solver {highs,cbc}`, `--time-limit`, `--max-split` (default 2; 1 = whole-bin moves only), `--no-fragment-avoidance` and `--fragments-only-receivers`. The last two reproduce the original scope.

Input: an Excel file with these sheets.
- `production_order(production_order, item_code, material_requirements[, priority])`
- `inventory(bin_no, item_code, stock_on_hand[, bin_capacity])`

Output sheets: `summary`, `bin_allocation_decision`, `bin_combination_decision`, `bin_status`, `allocation_by_item`, `combination_by_item`, `stock_check` and `rejected_orders`.

## Package layout
| module | role |
|---|---|
| `binopt/data.py` | loading and validation (types, duplicate bins, stock vs capacity), `stock_check`, order selection when stock is short |
| `binopt/allocation.py` | Problem 1 |
| `binopt/consolidation.py` | Problem 2 |
| `binopt/solver.py` | solver layer: HiGHS/CBC and lexicographic (multi-stage) solves with warm starts |
| `binopt/verify.py` | independent checks of the final plan |
| `binopt/pipeline.py` | end-to-end run, KPIs and Excel export |

## Issues found in the original Gurobi notebook
1. **The left-over was computed per allocation row, not per bin.** When a bin is shared by two orders, each row was compared with the full stock. This misclassified bins as fragments, and the stock check could even double-count.
2. **Problem 2 used every bin with a left-over, not only touched fragments.** It forced every origin into some destination ($\sum_n a_{mn}=1$), which allows a bin to be moved "into itself". Self-moves are counted as kept bins.
3. **Moves into bins that were already empty, and chains.** A bin could receive while it was also being moved out (A→B→C). In the old output, 18 of the 27 moves went into empty bins and there were 2 chains, so the plan could not be executed as written.
4. **The capacity constraint ignored the destination's own stock.** It used $\sum_m q_m a_{mn} \le c_n b_n$, so the receiving bin could overflow.
5. **Fragments were never avoided in allocation.** Problem 1 minimized pick lines only, although the same number of pick lines can often be reached while leaving fewer fragments.
6. **One dense model over every (order, item, bin) triple, using `x ≤ s·y`.** The bound was weak and the model was large. The `stock_check` column in Excel was used as given and could reject orders without any model decision.
7. **Excel output had 0-quantity rows** (151 rows in the combination sheet), and the result was not validated.

## Reframed formulation
The model is split by item, because a bin holds one item only. Every problem below is solved **per item** and **lexicographically**: each objective is optimized and frozen, and the next one only breaks ties. Tie-break stages are warm-started from the previous solution.

### 0. Order selection (only when total stock is short)
Choose orders with $u_i\in\{0,1\}$ such that $\sum_i d_{ij} u_i \le \sum_k s_{jk}$ for every item $j$. The objective is to maximize priority first, then quantity served. An order is served completely or not at all.

### Problem 1: allocation (item $j$, orders $I_j$, bins $K_j$, threshold $T$)
| variable | meaning |
|---|---|
| $x_{ik}\in\mathbb{Z}_+$ | quantity taken from bin $k$ for order $i$ |
| $y_{ik}\in\{0,1\}$ | pick line $(i,k)$ is used |
| $z_k\in\{0,1\}$ | bin $k$ is touched |
| $e_k\in\{0,1\}$ | bin $k$ is emptied |
| $f_k\in\{0,1\}$ | bin $k$ becomes a fragment |

$$\sum_k x_{ik}=d_i,\quad x_{ik}\le \min(s_k,d_i)\,y_{ik},\quad y_{ik}\le z_k,\quad r_k=s_k-\sum_i x_{ik}\ge 0$$
$$r_k\le s_k(1-e_k),\qquad r_k\ge T\,(z_k-e_k-f_k)$$
Objectives: **(1)** $\min\sum y_{ik}$ (pick lines), then **(2)** $\min\sum f_k$ (fragments created), then **(3)** $\min\sum z_k$ (bins opened).

### Problem 2: consolidation (item $j$, left-over $r_k$, capacity $C_k$)
- $O$ is the set of fragments: touched bins with $0<r_k<T$.
- $R$ is the set of possible receivers: every bin with $r_k>0$, or only $O$ with `--fragments-only-receivers`.
- $w_m\in\{0,1\}$: fragment $m$ is emptied **completely**.
- $a_{mn}\in\{0,1\}$: route $m\to n$ ($n\ne m$) is used, one forklift move.
- $q_{mn}\in\mathbb{Z}_+$: quantity moved on route $m\to n$.
- $g_n\in\{0,1\}$ marks a fragment that stays below $T$.
- $S$ = `max_split` (default 2): a fragment may be split over at most $S$ receiving bins.

$$\sum_n q_{mn}=r_m w_m,\qquad \sum_n a_{mn}\le S\,w_m,\qquad a_{mn}\le q_{mn}\le \min(r_m,\,C_n-r_n)\,a_{mn}$$
$$r_n+\sum_m q_{mn}\le C_n,\qquad a_{mn}+w_n\le 1\ \ (n\in O)\ \text{(no chains)},\qquad r_n+\sum_m q_{mn}\ \ge\ T\,(1-w_n-g_n)$$
Objectives: **(1)** $\max\sum w_m$ (bins freed), then **(2)** $\min\sum g_n$ (fragments remaining), then **(3)** $\min\sum a_{mn}$ (moves, so a split is used only when it pays off), then **(4)** $\min\sum r_m w_m$ (quantity moved).

Example: three fragments of 900 each. A whole-bin move gives 1,800 + 900, so one fragment remains. A 450/450 split gives 1,350 + 1,350: one bin is still freed and no fragment remains.

## Results on `input_template.xlsx` (16 orders, 151 pick lines)
| | Old Gurobi notebook | New, original scope<br>`--no-fragment-avoidance --fragments-only-receivers` | **New, default** |
|---|---|---|---|
| Pick lines (proven optimal) | 151 | 151 | **151** |
| Fragments after allocation | 27* | 27 | **25** |
| Moves / quantity moved | 27 / 6,475 (2 self-moves, 18 into empty bins, 2 chains) | 21 / 2,897 | **22 / 3,524** |
| Occupied touched bins: before → after | 43 → 22 | 43 → 22 | **42 → 20** |
| Fragments split | – | – | **0** (not needed on this data) |
| Fragments remaining | 3 | – | **2** (unavoidable†) |
| Plan validated | no | yes | **yes** |
| Runtime | – | ~57 s | ~150 s |

\* Counted per bin after fixing the per-row bug. † A58 (952) and A6 (870): the whole remaining stock of each item is below 1,000, so it ends in a single bin that is still a fragment.

Every stage of every per-item model finishes with status *Optimal*. Every result passes `binopt/verify.py`: demand met exactly, stock and capacity respected, no chains, no self-moves, and every moved fragment is emptied completely and split over at most 2 bins.

## Solver notes
- **HiGHS** is the default (`pip install highspy`). **CBC** can be selected with `--solver cbc` if a PuLP build with the CBC binary is installed.
- `time_limit` (60 s) applies to the primary stage of each model and `tiebreak_time_limit` (15 s) to the tie-break stages. If a stage hits its time limit with a feasible solution, that solution is kept and reported as `TimeLimit`. If it finds nothing better, the previous stage's solution is kept and reported as `NoImprovement`. The KPI `pick_stage_not_proven_optimal` counts the items whose pick stage was not proven optimal.
