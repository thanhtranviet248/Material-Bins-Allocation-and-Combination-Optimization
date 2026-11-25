# Material Bin Allocation and Consolidation

Plans two warehouse decisions for a manufacturer:

1. **Allocation**: which bins to pick from for each production order.
2. **Consolidation**: how to merge the small leftovers afterwards, so that fewer bins stay occupied.

Both decisions are solved as mixed-integer linear programs (MILP) with open-source tools:
**PuLP** (modelling) + **HiGHS** (solver, MIT licence).

---

## Contents

- [Problem statement](#problem-statement)
- [Approach](#approach)
  - [0. Order selection](#0-order-selection)
  - [Problem 1: allocation](#problem-1-allocation)
  - [Problem 2: consolidation](#problem-2-consolidation)
  - [Validation](#validation)
- [Results](#results)
- [Quick start](#quick-start)
- [Input and output](#input-and-output)
- [Package layout](#package-layout)
- [Solver notes](#solver-notes)

---

## Problem statement

### Setting

- The factory receives several production orders (PO1, PO2, ...).
- Each order needs given quantities of several materials (items).
- Materials are stored in bins:
  - a bin holds **one item only**;
  - a bin has a capacity, **3,000 units** by default;
  - one item can be spread over many bins.

### Problem 1: allocation

Decide how much each production order takes from each bin, so that:

- every requirement is met exactly;
- no bin gives more than its stock;
- as few **pick lines** (order × bin) as possible are used.

### Problem 2: consolidation

After allocation, a bin that was picked from and keeps a leftover **below 1,000 units**
is called a *fragment*.
Fragments are moved into other bins of the same item, to reduce the number of occupied bins.

Business rules:

- A fragment can be moved into **any bin of the same item**: untouched, partially picked or another fragment.
  The receiving bin's capacity must not be exceeded (its own stock included).
- A fragment that is moved is **emptied completely**.
- Its content may be split over **at most 2** receiving bins, to keep operations simple.
- A bin that is emptied cannot receive at the same time (no chains A → B → C).
- No bin is moved into itself.

### Stock shortage

When the total stock of an item is short, orders are served all-or-nothing:
only orders that can be served **completely** are kept, chosen by [order selection](#0-order-selection).
The other orders are reported as rejected.

---

## Approach

A bin holds one item only, so allocation and consolidation separate by item:
Problems 1 and 2 are solved as one small MILP **per item**.
Order selection links the items (an order needs several items), so it is one model over all items.

Each model has several objectives, solved **lexicographically**:

1. an objective is optimized, then its value is frozen as a constraint;
2. the next objective only breaks ties among equally good solutions;
3. each tie-break stage is warm-started from the previous solution.

### 0. Order selection

Only used when the total stock of at least one item is short; otherwise every order is kept.

Data: orders $i \in I$ with priority $p_i$, items $j \in J$,
demand $d_{ij}$ of order $i$ for item $j$, bins $K_j$ of item $j$ with stock $s_k$.

Choose orders with $u_i \in \{0,1\}$ ($u_i = 1$: order $i$ is served) such that

$$
\sum_{i \in I} d_{ij}\, u_i \;\le\; \sum_{k \in K_j} s_k \qquad \forall j \in J.
$$

Objectives, in order:

1. $\max \sum_{i \in I} p_i\, u_i$: **total priority**.
2. $\max \sum_{i \in I} \sum_{j \in J} d_{ij}\, u_i$: **total quantity served**.

An order is served completely or not at all.
Total stock per item is enough to guarantee that Problem 1 stays feasible, because a bin may be shared by several orders.

### Problem 1: allocation

Data for item $j$: orders $I_j$ with demand $d_i$ (of order $i$ for item $j$),
bins $K_j$ with stock $s_k > 0$, fragment threshold $T$.

| Variable | Meaning |
|---|---|
| $x_{ik} \in \mathbb{Z}_+$ | quantity taken from bin $k$ for order $i$ |
| $y_{ik} \in \{0,1\}$ | pick line $(i,k)$ is used |
| $z_k \in \{0,1\}$ | bin $k$ is touched |
| $e_k \in \{0,1\}$ | bin $k$ is emptied |
| $f_k \in \{0,1\}$ | bin $k$ becomes a fragment |

Constraints, with leftover $r_k = s_k - \sum_{i \in I_j} x_{ik}$:

$$
\begin{aligned}
\sum_{k \in K_j} x_{ik} &= d_i && \forall i \in I_j && \text{demand met exactly} \
x_{ik} &\le \min(s_k, d_i)\, y_{ik} && \forall i \in I_j,\ k \in K_j && \text{pick only on an open line} \
y_{ik} &\le z_k && \forall i \in I_j,\ k \in K_j && \text{a line touches its bin} \
r_k &\ge 0 && \forall k \in K_j && \text{stock respected} \
r_k &\le s_k\, (1 - e_k) && \forall k \in K_j && \text{emptied bin has nothing left} \
r_k &\ge T\, (z_k - e_k - f_k) && \forall k \in K_j && \text{small leftover} \Rightarrow \text{fragment} \
e_k \le z_k, \quad f_k &\le z_k && \forall k \in K_j && \text{only a touched bin is emptied or a fragment}
\end{aligned}
$$

Objectives, in order:

1. $\min \sum_{i \in I_j} \sum_{k \in K_j} y_{ik}$: **pick lines**.
2. $\min \sum_{k \in K_j} f_k$: **fragments created**.
   Among plans with the same number of pick lines, the one that leaves fewer small leftovers is preferred.
3. $\min \sum_{k \in K_j} z_k$: **bins opened**.

### Problem 2: consolidation

Data for item $j$: leftover $r_k$ after allocation, capacity $C_k$, threshold $T$.

| Symbol | Meaning |
|---|---|
| $O$ | fragments: touched bins with $0 < r_k < T$ |
| $R$ | possible receivers: every bin of the item with $r_k > 0$ (only $O$ with `--fragments-only-receivers`) |
| $P$ | possible routes: pairs $(m,n)$ with $m \in O$, $n \in R$, $n \ne m$ and $\min(r_m,\ C_n - r_n) > 0$ |
| $S$ | maximum split: `max_split` (default 2) |
| $w_m \in \{0,1\}$ | fragment $m \in O$ is emptied completely |
| $a_{mn} \in \{0,1\}$ | route $(m,n) \in P$ is used: one forklift move |
| $q_{mn} \in \mathbb{Z}_+$ | quantity moved on route $(m,n) \in P$ |
| $g_n \in \{0,1\}$ | fragment $n \in O$ stays and is still below $T$ |

Constraints (every sum runs over the routes in $P$):

$$
\begin{aligned}
\sum_{n} q_{mn} &= r_m\, w_m && \forall m \in O && \text{emptied completely, or not moved} \
\sum_{n} a_{mn} &\le S\, w_m && \forall m \in O && \text{split over at most } S \text{ bins} \
a_{mn} \;\le\; q_{mn} &\le \min(r_m,\ C_n - r_n)\, a_{mn} && \forall (m,n) \in P && \text{route link} \
r_n + \sum_{m} q_{mn} &\le C_n && \forall n \in R && \text{receiver capacity} \
a_{mn} + w_n &\le 1 && \forall (m,n) \in P,\ n \in O && \text{no chains} \
r_n + \sum_{m} q_{mn} &\ge T\, (1 - w_n - g_n) && \forall n \in O && \text{marks fragments that remain small}
\end{aligned}
$$

Objectives, in order:

1. $\max \sum_{m \in O} w_m$: **bins freed**.
2. $\min \sum_{n \in O} g_n$: **fragments remaining**.
3. $\min \sum_{(m,n) \in P} a_{mn}$: **forklift moves**.
   A split is used only when it frees more bins or leaves fewer fragments.
4. $\min \sum_{m \in O} r_m\, w_m$: **quantity moved**.

> **Example.** An item has only three bins, each with capacity 2,000, and each is a fragment holding 900 units
> (threshold 1,000). The total, 2,700, does not fit in one bin, so at most one bin can be freed.
>
> - Moving one whole bin into another gives 1,800 + 900: one bin is freed, but one fragment remains.
> - Splitting it over the other two, for example 450 / 450, gives 1,350 + 1,350: one bin is still freed,
>   and no fragment remains. Any split that brings both receivers to at least 1,000 is equally good.
>
> Both plans free one bin (objective 1); the split wins on objective 2, although it needs one more move.

### Validation

`binopt/verify.py` checks every final plan, independently of the models:

- demand is met exactly, and every bin is used only for its own item;
- stock and capacity are respected, and stock is conserved;
- every move stays within one item, with a positive quantity;
- there are no chains and no self-moves;
- every moved fragment is emptied completely and split over at most `max_split` bins.

---

## Results

On `input_template.xlsx` (16 production orders):

| KPI | Value |
|---|---|
| Pick lines | 151 |
| Fragments after allocation | 25 |
| Moves / quantity moved | 22 / 3,524 |
| Fragments split | 0 (not needed on this data) |
| Occupied bins of the demanded items, before → after consolidation | 42 → 20 |
| Fragments remaining | 2 (unavoidable \*) |
| Runtime | about 2.5 min |

\* Bin 1486 of item A58 (952 units) and bin 1549 of item A6 (870 units):
the whole remaining stock of each of these items is below 1,000,
so it ends up in a single bin that is still a fragment.

The plan passes validation.
The pick-line stage and the bins-opened stage of every item finish with status *Optimal*.
The fragments-created stage of Problem 1 reaches its 15 s tie-break limit on every item (status *TimeLimit*):
its plan is feasible, but not proven optimal.
Every stage of Problem 2 finishes with status *Optimal*.

---

## Quick start

### Install, run and test

```bash
pip install -r requirements.txt

# run the CLI
python -m binopt input_template.xlsx -o output_template.xlsx

# run the tests (22 tests)
python -m pytest -q
```

### Python API

Also used by `bin_allocation_and_consolidation.ipynb`.

```python
from binopt import Settings, load_input, run_pipeline, export_excel

settings = Settings(
    default_capacity=3000,
    fragment_threshold=1000,
    max_split=2,
    solver="highs",
)

demand, inventory = load_input("input_template.xlsx", settings)
result = run_pipeline(demand, inventory, settings)

print(result.kpis())
export_excel(result, "output.xlsx")
```

### CLI options

| Option | Meaning |
|---|---|
| `-o`, `--output` | output Excel file (default `output_template.xlsx`) |
| `--capacity` | bin capacity when the input has no `bin_capacity` column (default 3,000) |
| `--threshold` | fragment threshold (default 1,000) |
| `--max-split` | max receiving bins per fragment (default 2; 1 = whole-bin moves only) |
| `--solver {highs,cbc}` | MILP solver (default HiGHS) |
| `--time-limit` | time limit in seconds for the primary stage of each model (default 60) |
| `--no-fragment-avoidance` | skip the "fragments created" stage of Problem 1 |
| `--fragments-only-receivers` | fragments may only be merged into other fragments |
| `-v`, `--verbose` | show the solver log |

---

## Input and output

### Input

An Excel file with two sheets:

| Sheet | Columns |
|---|---|
| `production_order` | `production_order`, `item_code`, `material_requirements`, `priority` (optional) |
| `inventory` | `bin_no`, `item_code`, `stock_on_hand`, `bin_capacity` (optional) |

### Output

An Excel file with these sheets:

| Sheet | Content |
|---|---|
| `summary` | overall KPIs |
| `bin_allocation_decision` | quantity taken from each bin for each order |
| `bin_consolidation_decision` | each move: origin, destination, quantity |
| `bin_status` | bins touched by either step: stock, leftover, final quantity and status |
| `allocation_by_item` | allocation KPIs and solver status per item |
| `consolidation_by_item` | consolidation KPIs and solver status per item |
| `stock_check` | demand vs stock per item |
| `rejected_orders` | orders dropped because of a stock shortage |

---

## Package layout

| Module | Role |
|---|---|
| `binopt/data.py` | loading and validation (types, duplicate bins, stock vs capacity), stock check, order selection |
| `binopt/allocation.py` | Problem 1 |
| `binopt/consolidation.py` | Problem 2 |
| `binopt/solver.py` | solver layer: HiGHS / CBC, lexicographic solves with warm starts |
| `binopt/verify.py` | independent checks of the final plan |
| `binopt/pipeline.py` | end-to-end run, KPIs, Excel export |

---

## Solver notes

- **HiGHS** is the default (`pip install highspy`).
- **CBC** can be used with `--solver cbc`, if the CBC binary is installed.
- Time limits:
  - the primary stage of each model has `time_limit` (60 s);
  - each tie-break stage has `tiebreak_time_limit` (15 s).
- When a stage hits its limit:
  - if it has a feasible solution, that solution is kept and the stage is reported as `TimeLimit`
    (feasible, optimality not proven);
  - if a tie-break stage returns no solution, the previous stage's solution is kept
    and the stage is reported as `NoImprovement`;
  - if the primary stage finds no solution at all, the run stops with an error.
- The KPI `pick_stage_not_proven_optimal` counts the items whose pick-line stage was not proven optimal.
