# Material Bins Allocation and Combination Optimization
This project is where I use Gurobi solver on Python environment to address the material bins allocation and combination problem for a manufacturer.

## Problem statement
- At the factory, we receive multiple production orders (PO1, PO2, ...) from customers.
- Each production order requires specific quantities of materials. For example, order PO1 might need 14,219 units of material A57, 12,220 units of material A1, and 7,582 units of material A7.
- In the warehouse, materials are stored in bins (bin 1, bin 2, ...) with a capacity of 3,000 units each.
- Each bin can only store one type of material, but the same material can be stored across multiple bins. For instance, bin 1 holds 2,001 units of A1, bin 2 stores 2,155 units of A1, and bin 28 contains 2,504 units of A10.
- Objective 1: Develop a model to automatically assign bins and the corresponding quantities of materials to production orders. The model should fulfill all material requirements while minimizing the number of bins allocated and ensuring available stock is not exceeded.
- Objective 2: After allocation, there may be leftover inventory in some bins. If an amount of materials from a bin is allocated to any production orders and the left-over inventory in that bin is less than 1,000 units, it should be combined with other bins to reduce warehouse fragmentation. A model should be built to automatically identify and consolidate such bins, aiming to minimize the total number of bins used while respecting the bin capacity constraint.

## Solving process
- Step 1: Build a MILP optimization model to address objective 1.
- Step 2: Compare the allocated materials amount versus stock on-hand to check the left-over inventory in each bin.
- Step 3: Build another MILP optimization model to address objective 2.
- Step 4: Export the result to Excel for operation.

# Objective 1: Minimizing the number of bins allocated
## Mathematical model
### Sets
- I is the set of factory production orders, $I = \{PO1, PO2, PO3, \dots\}$
- J is the set of materials, $J = \{A1, A2, A3, \dots}$
- K is the set of bins, $K = \{1, 2, 3, \dots}$
- D is the set of demand (material requirements of production orders)
- S is the set of supply (stock on-hand of bins)

### Parameters
- i is the index of a production order, $\forall i \in I$
- j is the index of a material, $\forall j \in J$
- k is the index of a bin, $\forall k \in K$
- $d_{ij}$ is the demand of production order i for material j
- $s_{jk}$ is the supply of material j at bin k

### Decision variables
- $x_{ijk}$ is the amount of material j from bin k to be allocated to production order i, $x_{ijk} \in N$
- $y_{ijk}$ is the decision whether to allocate material j from bin k to production order i or not, $y_{ijk} = \{0, 1\}$

### Constraints
- Demand constraint: For each production order i and material j, the total amount of materials to be allocated from all bins k must satisfy the demand: $\sum_{k=1}^{K} x_{ijk} = d_{ij}, \forall i \in I, \forall j \in J$
- Supply constraint: For each material j and bin k, the total amount of materials to be allocated to all production orders i must not exceed the stock on-hand level: $\sum_{i=1}^{I} x_{ijk} \leq s_{jk}, \forall j \in J, \forall k \in K$
- Linking constraint: For each production order i, item j and bin k, if any amount of item j in bin k is allocated to production order i (y_{ijk} > 0), the corresponding decision variable x_{ijk} must be equal to 1: $x_{ijk} - s_{jk} * y_{ijk} \leq 0, \forall i in I, \forall j \in J, \forall k \in K$

### Objective function
- Minimizing the number of bins allocated: $\min \sum_{i=1}^{I} \sum_{j=1}^{J} \sum_{k=1}^{K} x_{ijk}$


