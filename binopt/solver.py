"""Thin solver layer on top of PuLP so the models are solver-agnostic.

Default back-end is HiGHS (MIT licence, https://highs.dev) through ``highspy``.
CBC (EPL licence) is supported as an alternative when its binary is available.
"""
from __future__ import annotations

from dataclasses import dataclass

import pulp

from .config import Settings


class SolverError(RuntimeError):
    pass


def new_var(prob: pulp.LpProblem, name: str, low=None, up=None, cat: str = "Continuous"):
    """Create a variable on PuLP 4.x (``prob.add_variable``) or PuLP 2.x/3.x (``LpVariable``)."""
    if hasattr(prob, "add_variable"):
        return prob.add_variable(name, low, up, cat=cat)
    return pulp.LpVariable(name, low, up, cat=cat)


def _status(ret) -> tuple[str, bool | None]:
    """Normalise ``prob.solve`` output across PuLP versions -> (status, has_solution or None if unknown)."""
    if hasattr(ret, "status"):  # PuLP 4.x returns LpSolveStats
        st = ret.status
        return getattr(st, "name", str(st)), bool(getattr(ret, "has_solution", True))
    return pulp.LpStatus[ret], None  # PuLP 2.x/3.x returns an int code


def make_solver(settings: Settings, time_limit: float | None = None, warm_start: bool = False):
    name = settings.solver.lower()
    limit = settings.time_limit if time_limit is None else time_limit
    if name == "highs":
        kwargs = dict(msg=settings.msg, timeLimit=limit, gapRel=settings.mip_gap, warmStart=warm_start)
        if settings.threads:
            kwargs["threads"] = settings.threads
        solver = pulp.HiGHS(**kwargs)
    elif name == "cbc":
        if not hasattr(pulp, "PULP_CBC_CMD"):
            raise SolverError("CBC is not available in this PuLP build; install the CBC binary or use 'highs'.")
        solver = pulp.PULP_CBC_CMD(msg=settings.msg, timeLimit=limit, gapRel=settings.mip_gap,
                                   threads=settings.threads, warmStart=warm_start)
    else:
        raise ValueError(f"Unknown solver '{settings.solver}'. Use 'highs' or 'cbc'.")
    if not solver.available():
        raise SolverError(f"Solver '{settings.solver}' is not available in this environment.")
    return solver


@dataclass
class Stage:
    name: str
    expr: pulp.LpAffineExpression
    sense: int = pulp.LpMinimize
    integral: bool = True
    """If True the optimal value is rounded before being frozen for the next stage."""


@dataclass
class StageResult:
    name: str
    status: str
    value: float


def _snapshot(prob):
    return [(v, v.value()) for v in prob.variables()]


def _clip(var, val: float) -> float:
    """Clean solver round-off so the value is accepted as a MIP start."""
    if var.cat in ("Integer", "Binary") or var.isInteger():
        val = round(val)
    if var.lowBound is not None:
        val = max(val, var.lowBound)
    if var.upBound is not None:
        val = min(val, var.upBound)
    return val


def _restore(snapshot):
    for v, val in snapshot:
        v.varValue = val


def solve_lexicographic(prob: pulp.LpProblem, stages: list[Stage], settings: Settings) -> list[StageResult]:
    """Optimise ``stages`` in priority order (pre-emptive goal programming).

    After each stage the achieved value is frozen as a constraint, so later
    stages can only break ties of earlier ones. Later stages are warm-started
    from the previous solution, which is always feasible for them; if a
    tie-break stage cannot improve within its time limit the previous solution
    is kept. Status ``TimeLimit`` means "feasible, optimality not proven".
    """
    primary = make_solver(settings)
    tiebreak = make_solver(settings, time_limit=settings.tiebreak_time_limit, warm_start=True)
    results: list[StageResult] = []
    for idx, stage in enumerate(stages):
        prob.setObjective(stage.expr)
        prob.sense = stage.sense
        snap = None
        if idx > 0:
            snap = _snapshot(prob)
            for v, val in snap:
                if val is not None:
                    v.setInitialValue(_clip(v, val))
        status, has_solution = _status(prob.solve(tiebreak if idx > 0 else primary))
        value = pulp.value(stage.expr)
        if status in ("Infeasible", "Unbounded"):
            raise SolverError(f"{prob.name}: stage '{stage.name}' returned status {status}.")
        if value is None or has_solution is False:
            if snap is None:
                raise SolverError(f"{prob.name}: stage '{stage.name}' found no solution (status {status}).")
            _restore(snap)  # keep the previous (feasible) plan
            value = pulp.value(stage.expr)
            status = "NoImprovement"
        if stage.integral:
            value = round(value)
        results.append(StageResult(stage.name, status, float(value)))
        if idx < len(stages) - 1:
            tol = 0.0 if stage.integral else 1e-6 * max(1.0, abs(value))
            if stage.sense == pulp.LpMinimize:
                prob += stage.expr <= value + tol, f"freeze_{idx}_{stage.name}"
            else:
                prob += stage.expr >= value - tol, f"freeze_{idx}_{stage.name}"
    return results


def int_value(var: pulp.LpVariable) -> int:
    v = var.value()
    return 0 if v is None else int(round(v))
