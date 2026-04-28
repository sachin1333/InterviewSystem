"""Problem-bank loader and deterministic picker for Phase 2 sessions."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.domain import Dimension, Problem, ProblemId


class ProblemBankError(ValueError):
    """Raised when a problem-bank YAML file is malformed."""


@dataclass(frozen=True)
class ProblemBankEntry:
    """A validated problem plus non-runtime metadata from the bank."""

    problem: Problem
    tags: tuple[str, ...]


class ProblemBank:
    """Validated collection of interview problems.

    The bank is immutable after construction. Selection is deterministic for a
    given ``session_id`` and attempts to cover as many target rubric dimensions
    as possible within the requested 3-4 problem sequence.
    """

    def __init__(self, entries: Sequence[ProblemBankEntry]) -> None:
        if not entries:
            raise ProblemBankError("problem bank must contain at least one problem")
        ids = [entry.problem.id for entry in entries]
        if len(ids) != len(set(ids)):
            duplicates = sorted({pid for pid in ids if ids.count(pid) > 1})
            raise ProblemBankError(f"duplicate problem id(s): {', '.join(duplicates)}")
        self._entries: tuple[ProblemBankEntry, ...] = tuple(entries)
        self._tags_by_id: dict[ProblemId, tuple[str, ...]] = {
            entry.problem.id: entry.tags for entry in self._entries
        }

    @classmethod
    def from_yaml(cls, path: str | Path) -> ProblemBank:
        """Load and validate a problem bank from YAML."""
        source = Path(path)
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ProblemBankError(f"{source}: root must be a mapping")
        raw_problems = raw.get("problems")
        if not isinstance(raw_problems, list):
            raise ProblemBankError(f"{source}: 'problems' must be a list")

        entries: list[ProblemBankEntry] = []
        errors: list[str] = []
        for idx, item in enumerate(raw_problems):
            try:
                entries.append(_parse_entry(item, idx))
            except ProblemBankError as exc:
                errors.extend(str(exc).split("; "))

        if errors:
            joined = "; ".join(errors)
            raise ProblemBankError(f"{source}: {joined}")
        return cls(entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def problems(self) -> tuple[Problem, ...]:
        """All validated problems in authoring order."""
        return tuple(entry.problem for entry in self._entries)

    def tags_for(self, problem_id: ProblemId) -> tuple[str, ...]:
        """Return tags for a problem, or an empty tuple if unknown."""
        return self._tags_by_id.get(problem_id, ())

    def pick_sequence(self, session_id: str, *, count: int | None = None) -> list[Problem]:
        """Return a deterministic, balanced sequence for ``session_id``.

        Defaults to 4 problems when available, otherwise all available problems.
        Explicit ``count`` is clamped to the bank size and must be positive.
        """
        if count is None:
            count = min(4, len(self._entries))
        if count <= 0:
            raise ProblemBankError("pick_sequence count must be positive")
        count = min(count, len(self._entries))

        ranked = sorted(
            self.problems,
            key=lambda problem: _stable_rank(session_id, problem.id),
        )
        selected = list(ranked[:count])
        selected_coverage = _coverage_size(selected)
        best_possible = max(
            _coverage_size(ranked[start : start + count])
            for start in range(0, len(ranked) - count + 1)
        )

        # Preserve hash fairness unless a one-for-one swap improves rubric-dimension
        # coverage for this session's sequence.
        if selected_coverage < best_possible:
            for candidate in ranked[count:]:
                for idx in range(len(selected)):
                    proposal = [*selected[:idx], candidate, *selected[idx + 1 :]]
                    if _coverage_size(proposal) > selected_coverage:
                        selected = proposal
                        selected_coverage = _coverage_size(selected)
                        break
                if selected_coverage >= best_possible:
                    break

        return selected

    def prewarm_openers(self) -> tuple[str, ...]:
        """Return all candidate-visible opener strings for cold-start caching."""
        return tuple(problem.opener_text for problem in self.problems)


def _parse_entry(item: object, idx: int) -> ProblemBankEntry:
    prefix = f"problems[{idx}]"
    errors: list[str] = []
    if not isinstance(item, dict):
        raise ProblemBankError(f"{prefix}: entry must be a mapping")

    problem_id = _required_str(item, "id", f"{prefix}.id", errors)
    opener = _required_str(item, "opener", f"{prefix}.opener", errors)
    context = _required_str(item, "context", f"{prefix}.context", errors)
    target_dimensions = _dimensions(item.get("target_dimensions"), f"{prefix}.target_dimensions", errors)
    thresholds = _thresholds(item.get("dim_thresholds"), f"{prefix}.dim_thresholds", errors)
    expected_duration_s = _positive_int(
        item.get("expected_duration_s"), f"{prefix}.expected_duration_s", errors
    )
    tags = _tags(item.get("tags"), f"{prefix}.tags", errors)

    if target_dimensions:
        missing_thresholds = [dim.value for dim in target_dimensions if dim not in thresholds]
        if missing_thresholds:
            errors.append(
                f"{prefix}.dim_thresholds missing target dimension(s): "
                + ", ".join(missing_thresholds)
            )
    extra_thresholds = [dim.value for dim in thresholds if dim not in target_dimensions]
    if extra_thresholds:
        errors.append(
            f"{prefix}.dim_thresholds contains non-target dimension(s): "
            + ", ".join(extra_thresholds)
        )

    if errors:
        raise ProblemBankError("; ".join(errors))

    return ProblemBankEntry(
        problem=Problem(
            id=ProblemId(problem_id),
            opener_text=opener,
            context=context,
            target_dimensions=target_dimensions,
            dim_thresholds=thresholds,
            expected_duration_s=expected_duration_s,
        ),
        tags=tags,
    )


def _required_str(item: Mapping[str, object], key: str, label: str, errors: list[str]) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return ""
    return value.strip()


def _dimensions(raw: object, label: str, errors: list[str]) -> tuple[Dimension, ...]:
    if not isinstance(raw, list) or not raw:
        errors.append(f"{label} must be a non-empty list")
        return ()
    dims: list[Dimension] = []
    for pos, value in enumerate(raw):
        if not isinstance(value, str):
            errors.append(f"{label}[{pos}] must be a dimension string")
            continue
        try:
            dim = Dimension(value)
        except ValueError:
            errors.append(f"{label}[{pos}] unknown dimension {value!r}")
            continue
        if dim in dims:
            errors.append(f"{label}[{pos}] duplicates dimension {value!r}")
            continue
        dims.append(dim)
    return tuple(dims)


def _thresholds(raw: object, label: str, errors: list[str]) -> dict[Dimension, float]:
    if not isinstance(raw, dict) or not raw:
        errors.append(f"{label} must be a non-empty mapping")
        return {}
    out: dict[Dimension, float] = {}
    for raw_key, raw_value in raw.items():
        if not isinstance(raw_key, str):
            errors.append(f"{label} keys must be dimension strings")
            continue
        try:
            dim = Dimension(raw_key)
        except ValueError:
            errors.append(f"{label}.{raw_key} unknown dimension {raw_key!r}")
            continue
        if not isinstance(raw_value, int | float) or not 0.0 <= float(raw_value) <= 1.0:
            errors.append(f"{label}.{raw_key} must be a number between 0 and 1")
            continue
        out[dim] = float(raw_value)
    return out


def _positive_int(raw: object, label: str, errors: list[str]) -> int:
    if not isinstance(raw, int) or raw <= 0:
        errors.append(f"{label} must be a positive integer")
        return 0
    return raw


def _tags(raw: object, label: str, errors: list[str]) -> tuple[str, ...]:
    if not isinstance(raw, list):
        errors.append(f"{label} must be a list")
        return ()
    tags: list[str] = []
    for pos, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}[{pos}] must be a non-empty string")
            continue
        tags.append(value.strip())
    return tuple(tags)


def _stable_rank(session_id: str, problem_id: ProblemId) -> int:
    digest = hashlib.sha256(f"{session_id}:{problem_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _coverage_size(problems: Sequence[Problem]) -> int:
    return len({dim for problem in problems for dim in problem.target_dimensions})
