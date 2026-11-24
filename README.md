# Materials Bins Allocation and Consolidation

This project plans two warehouse decisions for a manufacturer:
1. **Allocation:** which bins to pick from for each production order.
2. **Consolidation:** how to merge the small left-overs afterwards so that fewer bins stay occupied.

Both are solved as mixed-integer programs with open-source tools: **PuLP + HiGHS** (MIT licence).

## Problem statement
- The factory receives several production orders (PO1, PO2, ...). Each order needs given quantities of several materials (items).
- Materials are stored in bins. A bin holds one item only and has a capacity, 3,000 units by default. One item can be spread over many bins.
- **Problem 1, allocation:** decide how much each production order takes from each bin, so that:
  - every requirement is met exactly;
  - no bin gives more than its stock;
  - as few pick lines (order × bin) as possible are used.
- **Problem 2, consolidation:** after allocation, a bin that was picked from and keeps a left-over below 1,000 units is a *fragment*. Fragments are moved into other bins of the same item to reduce the number of occupied bins. The business rules are:
  - A fragment can be moved into any bin of the same item, whether untouched, partially picked or another fragment, as long as the receiving bin's capacity is not exceeded (its own stock included).
  - A fragment that is moved is emptied completely. Its content may be split over **at most 2** receiving bins, to keep operations simple.
  - A bin that is emptied cannot receive at the same time (no chains A→B→C), and no bin is moved into itself.
- When total stock of an item is short, only the orders that can be served completely are kept.

## Approach
A bin holds one item only, so the problem separates by item: every model below is a small MILP **per item**. Each model has several objectives, solved **lexicographically**: an objective is optimized and frozen, and the next one only breaks ties. Each tie-break stage is warm-started from the previous solution.

### 0. Order selection (only when total stock is short)
Choose orders with $u_i\in\{0,1\}$ such that $\sum_i d_{ij} u_i \le \sum_k s_{jk}$ for every item $j$. Maximize total priority first, then total quantity served. An order is served completely or not at all.

### Problem 1: allocation (item $j$, orders $I_j$ with demand $d_i$, bins $K_j$ with stock $s_k$, threshold $T$)
| variable | meaning |
|---|---|
| $x_{ik}\in\mathbb{Z}_+$ | quantity taken from bin $k$ for order $i$ |
| $y_{ik}\in\{0,1\}$ | pick line $(i,k)$ is used |
| $z_k\in\{0,1\}$ | bin $k$ is touched |
| $e_k\in\{0,1\}$ | bin $k$ is emptied |
| $f_k\in\{0,1\}$ | bin $k$ becomes a fragment |

$$\sum_k x_{ik}=d_i,\quad x_{ik}\le \min(s_k,d_i)\,y_{ik},\quad y_{ik}\le z_k,\quad r_k=s_k-\sum_i x_{ik}\ge 0$$
$$r_k\le s_k(1-e_k),\qquad r_k\ge T\,(z_k-e_k-f_k)$$
Objectives, in order:
1. $\min\sum y_{ik}$: pick lines.
2. $\min\sum f_k$: fragments created. Among plans with the same number of pick lines, the one that leaves fewer small left-overs is preferred.
3. $\min\sum z_k$: bins opened.

### Problem 2: consolidation (item $j$, left-over $r_k$ after allocation, capacity $C_k$)
| symbol | meaning |
|---|---|
| $O$ | fragments: touched bins with $0<r_k<T$ |
| $R$ | possible receivers: every bin of the item with $r_k>0$ |
| $w_m\in\{0,1\}$ | fragment $m$ is emptied completely |
| $a_{mn}\in\{0,1\}$ | route $m\to n$ ($n\ne m$) is used, one forklift move |
| $q_{mn}\in\mathbb{Z}_+$ | quantity moved on route $m\to n$ |
| $g_n\in\{0,1\}$ | fragment $n$ stays and is still below $T$ |
| $S$ | maximum split, `max_split` = 2 |

$$\sum_n q_{mn}=r_m w_m,\qquad \sum_n a_{mn}\le S\,w_m,\qquad a_{mn}\le q_{mn}\le \min(r_m,\,C_n-r_n)\,a_{mn}$$
$$r_n+\sum_m q_{mn}\le C_n,\qquad a_{mn}+w_n\le 1\ \ (n\in O),\qquad r_n+\sum_m q_{mn}\ \ge\ T\,(1-w_n-g_n)$$
Objectives, in order:
1. $\max\sum w_m$: bins freed.
2. $\min\sum g_n$: fragments remaining.
3. $\min\sum a_{mn}$: forklift moves. A split is used only when it frees more bins or leaves fewer fragments.
4. $\min\sum r_m w_m$: quantity moved.

Example: three fragments hold 900 each. Moving one whole bin gives 1,800 + 900, so one fragment remains. Splitting it 450/450 gives 1,350 + 1,350: one bin is still freed and no fragment remains.

### Validation
`binopt/verify.py` checks every final plan independently of the models:
- demand is met exactly;
- stock and capacity are respected, and stock is conserved;
- there are no chains and no self-moves;
- every moved fragment is emptied completely and split over at most 2 bins.

## Results on `input_template.xlsx` (16 orders)
| KPI | Value |
|---|---|
| Pick lines | 151 |
| Fragments after allocation | 25 |
| Moves / quantity moved | 22 / 3,524 |
| Fragments split | 0 (not needed on this data) |
| Occupied touched bins: before → after consolidation | 42 → 20 |
| Fragments remaining | 2 (unavoidable*) |
| Runtime | about 2.5 min |

\* A58 (952) and A6 (870): the whole remaining stock of each of these items is below 1,000, so it ends up in a single bin that is still a fragment.

Every stage of every per-item model finishes with status *Optimal*, and the plan passes validation.

## Quick start
```bash
pip install -r requirements.txt
python -m binopt input_template.xlsx -o output_template.xlsx      # CLI
python -m pytest -q                                              # 22 tests
```
Python API, also used by `bin_allocation_and_consolidation.ipynb`:
```python
from binopt import Settings, load_input, run_pipeline, export_excel
settings = Settings(default_capacity=3000, fragment_threshold=1000, max_split=2, solver="highs")
demand, inventory = load_input("input_template.xlsx", settings)
result = run_pipeline(demand, inventory, settings)
print(result.kpis()); export_excel(result, "output.xlsx")
```

CLI options:
| option | meaning |
|---|---|
| `--capacity` | default bin capacity (3,000) |
| `--threshold` | fragment threshold (1,000) |
| `--max-split` | max receiving bins per fragment (default 2; 1 = whole-bin moves only) |
| `--solver {highs,cbc}` | MILP solver (default HiGHS) |
| `--time-limit` | time limit in seconds for the primary stage of each model |
| `--no-fragment-avoidance` | skip the "fragments created" stage of Problem 1 |
| `--fragments-only-receivers` | fragments may only be merged into other fragments |

**Input:** an Excel file with these sheets.
- `production_order(production_order, item_code, material_requirements[, priority])`
- `inventory(bin_no, item_code, stock_on_hand[, bin_capacity])`

**Output sheets:** `summary`, `bin_allocation_decision`, `bin_consolidation_decision`, `bin_status`, `allocation_by_item`, `consolidation_by_item`, `stock_check` and `rejected_orders`.

## Package layout
| module | role |
|---|---|
| `binopt/data.py` | loading and validation (types, duplicate bins, stock vs capacity), stock check and order selection |
| `binopt/allocation.py` | Problem 1 |
| `binopt/consolidation.py` | Problem 2 |
| `binopt/solver.py` | solver layer: HiGHS/CBC and lexicographic solves with warm starts |
| `binopt/verify.py` | independent checks of the final plan |
| `binopt/pipeline.py` | end-to-end run, KPIs and Excel export |

## Solver notes
- **HiGHS** is the default (`pip install highspy`). **CBC** can be used with `--solver cbc` if the CBC binary is installed.
- The primary stage of each model has `time_limit` (60 s) and each tie-break stage has `tiebreak_time_limit` (15 s). When a stage hits its limit:
  - if it has a feasible solution, that solution is kept and the stage is reported as `TimeLimit`;
  - if it finds nothing better, the previous stage's solution is kept and the stage is reported as `NoImprovement`.
- The KPI `pick_stage_not_proven_optimal` counts the items whose pick-line stage was not proven optimal.
