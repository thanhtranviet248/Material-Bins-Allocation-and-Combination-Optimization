"""Run-time settings shared by every model in the pipeline."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Settings:
    # Warehouse rules
    default_capacity: int = 3000
    """Bin capacity used when the inventory sheet has no ``bin_capacity`` column."""
    fragment_threshold: int = 1000
    """A bin whose left-over is ``0 < left_over < fragment_threshold`` is a fragment."""

    # Modelling switches
    avoid_fragments_in_allocation: bool = True
    """Stage 2 of the allocation model: among minimum-pick plans, create as few fragments as possible."""
    allow_full_bin_receivers: bool = True
    """Consolidation may top up any bin of the same item that has spare room, not only other fragments."""
    max_split: int = 2
    """A fragment is always emptied completely, but its content may be split over at most this many
    receiving bins (1 = move the whole bin into a single receiver)."""
    include_untouched_fragments: bool = False
    """Also consolidate small bins that were already small before allocation (not touched by any order)."""

    # Solver
    solver: str = "highs"
    """``highs`` (default, via highspy) or ``cbc`` (needs the CBC binary that PuLP can find)."""
    time_limit: float | None = 60.0
    """Seconds for the primary stage of each model (e.g. pick lines); ``None`` = no limit."""
    tiebreak_time_limit: float | None = 15.0
    """Seconds for each tie-break stage. These start from the previous stage's solution (warm start),
    so hitting the limit still returns a valid plan that is optimal for the primary objective."""
    mip_gap: float | None = None
    """Relative MIP gap; ``None`` = solver default."""
    threads: int | None = None
    msg: bool = False
