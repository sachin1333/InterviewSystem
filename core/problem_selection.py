"""Deterministic profile-aware problem selection.

The selector is intentionally pure: it ranks already-validated
``ProblemBankEntry`` values without I/O or LLM calls.  Empty profiles delegate
to ``ProblemBank.pick_sequence`` so the existing balanced deterministic
fallback remains byte-for-byte equivalent for later integration.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core.domain import Dimension, Problem, ProblemId
from core.problem_bank import ProblemBank, ProblemBankEntry, ProblemBankError

_TERM_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ProfileFeatures:
    """Candidate profile text used to rank problem-bank entries."""

    role_text: str
    skills: tuple[str, ...]
    claims_text: str


@dataclass(frozen=True)
class ProblemSelection:
    """Selected problems plus stable rationale keyed by problem id."""

    problems: tuple[Problem, ...]
    selection_rationale: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class _RankedEntry:
    entry: ProblemBankEntry
    profile_score: int
    rationale: tuple[str, ...]
    stable_rank: int


def tokenize_terms(text: str) -> tuple[str, ...]:
    """Return lowercase alphanumeric terms in encounter order."""

    return tuple(_TERM_RE.findall(text.lower()))


class ProfileAwareProblemSelector:
    """Select a deterministic, profile-ranked, dimension-aware problem plan."""

    def select(
        self,
        session_id: str,
        profile: ProfileFeatures,
        entries: Sequence[ProblemBankEntry],
        *,
        count: int = 4,
    ) -> ProblemSelection:
        entry_tuple = tuple(entries)
        if not _has_profile_terms(profile):
            picked = ProblemBank(entry_tuple).pick_sequence(session_id, count=count)
            return ProblemSelection(
                problems=tuple(picked),
                selection_rationale={str(problem.id): () for problem in picked},
            )

        if not entry_tuple:
            raise ProblemBankError("problem bank must contain at least one problem")
        if count <= 0:
            raise ProblemBankError("pick_sequence count must be positive")
        count = min(count, len(entry_tuple))

        profile_terms = _profile_terms(profile)
        skill_terms = _skill_terms(profile.skills)
        ranked = sorted(
            (
                _rank_entry(session_id, entry, profile_terms=profile_terms, skill_terms=skill_terms)
                for entry in entry_tuple
            ),
            key=lambda item: (-item.profile_score, item.stable_rank),
        )

        selected = list(ranked[:count])
        selected = _improve_dimension_coverage(selected, ranked, count)
        return ProblemSelection(
            problems=tuple(item.entry.problem for item in selected),
            selection_rationale={
                str(item.entry.problem.id): item.rationale for item in selected
            },
        )


def _rank_entry(
    session_id: str,
    entry: ProblemBankEntry,
    *,
    profile_terms: frozenset[str],
    skill_terms: frozenset[str],
) -> _RankedEntry:
    problem = entry.problem
    tag_matches = _matched_tags(entry.tags, profile_terms=profile_terms, skill_terms=skill_terms)
    tag_term_overlap = len(_tag_terms(entry.tags) & profile_terms)
    text_terms = frozenset(tokenize_terms(f"{problem.opener_text} {problem.context}"))
    text_overlap = len(text_terms & profile_terms)
    profile_score = (100 * len(tag_matches)) + (25 * tag_term_overlap) + (10 * text_overlap)
    return _RankedEntry(
        entry=entry,
        profile_score=profile_score,
        rationale=tag_matches,
        stable_rank=_stable_rank(session_id, problem.id),
    )


def _improve_dimension_coverage(
    selected: list[_RankedEntry],
    ranked: Sequence[_RankedEntry],
    count: int,
) -> list[_RankedEntry]:
    target_coverage = min(count, len(_covered_dimensions(item.entry.problem for item in ranked)))
    selected_coverage = len(_covered_dimensions(item.entry.problem for item in selected))
    if selected_coverage >= target_coverage:
        return selected

    selected_ids = {item.entry.problem.id for item in selected}
    remaining = [item for item in ranked if item.entry.problem.id not in selected_ids]
    while selected_coverage < target_coverage:
        best_swap: tuple[int, _RankedEntry, int] | None = None
        for candidate in remaining:
            for idx, current in enumerate(selected):
                proposal = [*selected[:idx], candidate, *selected[idx + 1 :]]
                coverage = len(_covered_dimensions(item.entry.problem for item in proposal))
                if coverage <= selected_coverage:
                    continue
                score_loss = current.profile_score - candidate.profile_score
                swap_key = (coverage, -score_loss, -candidate.stable_rank)
                if best_swap is None or swap_key > (
                    best_swap[2],
                    -(selected[best_swap[0]].profile_score - best_swap[1].profile_score),
                    -best_swap[1].stable_rank,
                ):
                    best_swap = (idx, candidate, coverage)
        if best_swap is None:
            break
        idx, candidate, selected_coverage = best_swap
        outgoing = selected[idx]
        selected[idx] = candidate
        remaining = [
            item for item in remaining
            if item.entry.problem.id != candidate.entry.problem.id
        ]
        remaining.append(outgoing)

    return selected


def _has_profile_terms(profile: ProfileFeatures) -> bool:
    return bool(_profile_terms(profile))


def _profile_terms(profile: ProfileFeatures) -> frozenset[str]:
    terms: set[str] = set(tokenize_terms(profile.role_text))
    terms.update(tokenize_terms(profile.claims_text))
    for skill in profile.skills:
        terms.update(tokenize_terms(skill))
    return frozenset(terms)


def _skill_terms(skills: Sequence[str]) -> frozenset[str]:
    terms: set[str] = set()
    for skill in skills:
        terms.update(tokenize_terms(skill))
    return frozenset(terms)


def _matched_tags(
    tags: Sequence[str],
    *,
    profile_terms: frozenset[str],
    skill_terms: frozenset[str],
) -> tuple[str, ...]:
    matched: set[str] = set()
    for tag in tags:
        normalized = _normalize_tag(tag)
        terms = frozenset(tokenize_terms(tag))
        if normalized in profile_terms or terms & profile_terms or terms & skill_terms:
            matched.add(normalized)
    return tuple(sorted(matched))


def _tag_terms(tags: Sequence[str]) -> frozenset[str]:
    terms: set[str] = set()
    for tag in tags:
        terms.update(tokenize_terms(tag))
    return frozenset(terms)


def _covered_dimensions(problems: Iterable[Problem]) -> set[Dimension]:
    return {dimension for problem in problems for dimension in problem.target_dimensions}


def _normalize_tag(tag: str) -> str:
    return "-".join(tokenize_terms(tag))


def _stable_rank(session_id: str, problem_id: ProblemId) -> int:
    digest = hashlib.sha256(f"{session_id}:{problem_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")
