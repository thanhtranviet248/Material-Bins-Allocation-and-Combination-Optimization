# Material Bins Allocation and Combination Optimization
This project is where I use Gurobi solver on Python environment to optimize the material bins allocation and combination problem for a manufacturer.

## Problem statement
- At the factory, we receive multiple production orders (PO1, PO2, ...) from customers.
- Each production order requires specific quantities of materials. For example, order PO1 might need 14,219 units of material A57, 12,220 units of material A1, and 7,582 units of material A7.
- In the warehouse, materials are stored in bins (bin 1, bin 2, ...) with a capacity of 3,000 units each.
- Each bin can only store one type of material, but the same material can be stored across multiple bins. For instance, bin 1 holds 2,001 units of A1, bin 2 stores 2,155 units of A1, and bin 28 contains 2,504 units of A10.
- Objective 1: Develop a model to automatically assign bins and the corresponding quantities of materials to production orders. The model should fulfill all material requirements while minimizing the number of bins allocated and ensuring available stock is not exceeded.
- Objective 2: After allocation, there may be leftover inventory in some bins. If the leftover quantity in a bin is less than 1,000 units, it should be combined with other bins to reduce warehouse fragmentation. A model should be built to automatically identify and consolidate such bins, aiming to minimize the total number of bins used while respecting the bin capacity constraint.

## Solving process
- Step 1: Build a MILP optimization model to address objective 1.
- Step 2: Compare the allocated materials amount versus stock on-hand to check the left-over inventory in each bin.
- Step 3: Build another MILP optimization model to address objective 2.
- Step 4: Export the result to Excel for operation.

# Objective 1
## Mathematical model
### Sets
- I is the set of the factory workforce, $I = \{1, 2, 3, \dots, 20\}$
- J is the set of weekday, $J = \{1, 2, 3, \dots, 7\}$
- K is the set of each shift in a day, $K = \{1, 2, 3\}$

### Parameters
- $D_{jk}$ is the labor demand in day j at shift k

### Decision variable
- $x_{ijk}$ is the binary decision variable on assigning labor i to day j at shift k, $x_{ijk} = \{0, 1\}$

### Constraints
- For each shift k in each day j, the number of labor to be assigned must fulfill the demand: $\sum_{i=1}^{20} x_{ijk} = D_{jk}, \forall k \in K, \forall j \in J$
- For each labor i in each day j, the maximum number of shift he/she can work is 1: $\sum_{k=1}^{3} x_{ijk} \leq 1, \forall i \in I, \forall j \in J$
- For each labor i, the maximum day and shift he/she can work is 5: $\sum_{j=1}^{7} \sum_{k=1}^{3} x_{ijk} \leq 5, \forall i \in I$
- For each labor i in each 2 days j and j+1, if he/she works on evening shift (k = 3) today, he/she cannot work on the morning shift (k = 1) tomorrow: $x_{ij3} + x_{i(j+1)1} = 1, \forall j \in J, \forall j+1 \in J$

### Objective function
- Minimizing the total workforce assigned: $\min \sum_{i=1}^{20} \sum_{j=1}^{7} \sum_{k=1}^{3} x_{ijk}$


