"""CoverageTracker — per-problem, per-dimension signal accumulation.

Tasks 2.2.1 + 2.2.2.

Design
------
Each dimension accumulates signal via a diminishing-returns curve so that one
very strong answer cannot immediately saturate a dimension.  The update rule is:

    new = old + signal * (1 - old)

This means:
  - First strong signal (0.8) → 0.8
  - Same again          → 0.8 + 0.8 * 0.2 = 0.96
  - And again           → 0.96 + 0.8 * 0.04 ≈ 0.992

Saturation is only declared when accumulated >= threshold (default 0.8).  A
single answer that produces a signal of exactly 1.0 will reach ~1.0 in one
step — this is intentional for clear, comprehensive responses.

The tracker is purely in-memory; it is rebuilt by the session_runner from
``SignalEmitted`` events on each request (event-sourced pattern).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from core.domain import Dimension, ProblemId


@dataclass
class CoverageTracker:
    """Accumulate rubric-dimension signal for problems in a session.

    Parameters
    ----------
    default_threshold:
        Saturation threshold applied when ``Problem.dim_thresholds`` doesn't
        specify one for a given dimension.  Default 0.80.
    """

    default_threshold: float = 0.80

    # Internal: {problem_id: {dimension: accumulated_signal}}
    _acc: dict[ProblemId, dict[Dimension, float]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(float)),
        repr=False,
    )

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def record(self, problem_id: ProblemId, dim: Dimension, signal: float) -> None:
        """Accumulate *signal* (0..1) for *dim* under *problem_id*.

        Uses diminishing-returns curve: ``new = old + signal * (1 - old)``.
        Input *signal* is clamped to [0, 1].
        """
        signal = max(0.0, min(1.0, signal))
        old = self._acc[problem_id][dim]
        self._acc[problem_id][dim] = old + signal * (1.0 - old)

    def current(self, problem_id: ProblemId) -> Mapping[Dimension, float]:
        """Return accumulated signal map for *problem_id*.

        Missing dimensions are implicitly 0.0.
        """
        return dict(self._acc[problem_id])

    def under_served(
        self,
        problem_id: ProblemId,
        thresholds: Mapping[Dimension, float],
    ) -> list[Dimension]:
        """Return dims whose accumulated signal is below their threshold.

        Parameters
        ----------
        thresholds:
            Per-dim saturation thresholds, typically from ``Problem.dim_thresholds``.
            Dimensions not present in *thresholds* use ``self.default_threshold``.
        """
        acc = self._acc[problem_id]
        under: list[Dimension] = []
        # Use keys from thresholds if provided; otherwise check all Dimensions
        dims_to_check = set(thresholds.keys()) if thresholds else set(Dimension)
        for dim in dims_to_check:
            threshold = thresholds.get(dim, self.default_threshold)
            if acc.get(dim, 0.0) < threshold:
                under.append(dim)
        return under

    def is_saturated(
        self,
        problem_id: ProblemId,
        thresholds: Mapping[Dimension, float],
    ) -> bool:
        """True when all dimensions in *thresholds* have reached their threshold."""
        return len(self.under_served(problem_id, thresholds)) == 0

    def signal_map(self, problem_id: ProblemId) -> dict[Dimension, float]:
        """Full accumulated map including zero-signal dimensions from Dimension enum."""
        acc = dict(self._acc[problem_id])
        for dim in Dimension:
            acc.setdefault(dim, 0.0)
        return acc
