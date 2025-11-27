# qc_controller.py
# SPDX-License-Identifier: MIT
"""Helpers for inline QC execution and summary building."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .config import QCMode
from .interfaces import QualityScorer, Record, RunLifecycleHook, RunContext, RunArtifacts
from .log import get_logger
from .records import ensure_meta_dict, merge_meta_defaults, best_effort_record_path, filter_qc_meta
from .qc_utils import update_dup_family_counts, top_dup_families

log = get_logger(__name__)


@dataclass(slots=True)
class QCSummaryTracker:
    """Track QC scoring outcomes and duplicate families.

    near_dup is treated as a combined flag (Simhash OR MinHash). With
    drop_near_dups=True, any record flagged near-duplicate by either mechanism
    will be dropped. Duplicate families are keyed by dup_family_id with
    counts/examples for post-QC reporting.
    """
    enabled: bool = False
    mode: str = QCMode.INLINE
    min_score: Optional[float] = None
    drop_near_dups: bool = False
    scored: int = 0
    kept: int = 0
    dropped_low_score: int = 0
    dropped_near_dup: int = 0
    errors: int = 0
    candidates_low_score: int = 0
    candidates_near_dup: int = 0
    dup_families: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    top_dup_snapshot: List[Dict[str, Any]] = field(default_factory=list)

    def observe(self, qc_result: Dict[str, Any], *, apply_gates: bool = True) -> bool:
        """Update counters based on a QC row and return whether to keep it.

        Args:
            qc_result (dict[str, Any]): QC metrics for a single record.
            apply_gates (bool): Whether to apply scoring and near-duplicate
                drop rules.

        Returns:
            bool: True when the record should be kept.
        """
        self.scored += 1
        family_id = qc_result.get("dup_family_id") or qc_result.get("doc_id")
        path = qc_result.get("path")
        if family_id:
            update_dup_family_counts(self.dup_families, family_id, path)
            if self.top_dup_snapshot:
                self.top_dup_snapshot.clear()

        low_score = self._is_low_score(qc_result)
        # near_dup aggregates simhash + MinHash signals from the scorer
        near_dup = bool(qc_result.get("near_dup"))

        if low_score:
            self.candidates_low_score += 1
        if near_dup:
            self.candidates_near_dup += 1

        keep = True
        if apply_gates and low_score:
            self.dropped_low_score += 1
            keep = False
        elif apply_gates and self.drop_near_dups and near_dup:
            self.dropped_near_dup += 1
            keep = False

        if keep:
            self.kept += 1
        return keep

    def record_error(self) -> None:
        """Increment error count for a failed QC attempt."""
        self.errors += 1

    def as_dict(self) -> Dict[str, Any]:
        """Return a summary dictionary suitable for serialization."""
        return {
            "enabled": bool(self.enabled),
            "mode": self.mode,
            "min_score": self.min_score,
            "drop_near_dups": bool(self.drop_near_dups),
            "scored": int(self.scored),
            "kept": int(self.kept),
            "dropped_low_score": int(self.dropped_low_score),
            "dropped_near_dup": int(self.dropped_near_dup),
            "errors": int(self.errors),
            "candidates_low_score": int(self.candidates_low_score),
            "candidates_near_dup": int(self.candidates_near_dup),
            "top_dup_families": self.top_dup_families(),
        }

    def _is_low_score(self, qc_result: Dict[str, Any]) -> bool:
        """Return True when qc_result score falls below the configured min."""
        if self.min_score is None:
            return False
        score_value = qc_result.get("score")
        if score_value is None:
            return False
        try:
            return float(score_value) < float(self.min_score)
        except Exception:
            return False

    def top_dup_families(self) -> List[Dict[str, Any]]:
        """Return the largest duplicate families with cached snapshot reuse."""
        if self.top_dup_snapshot:
            return [dict(entry) for entry in self.top_dup_snapshot]
        return top_dup_families(self.dup_families)

    @classmethod
    def from_summary_dict(cls, data: Mapping[str, Any]) -> "QCSummaryTracker":
        """Rehydrate a tracker from a serialized summary dictionary.

        Args:
            data (Mapping[str, Any]): Summary produced by as_dict().

        Returns:
            QCSummaryTracker: Tracker populated with summary values.
        """
        tracker = cls()
        tracker.enabled = bool(data.get("enabled"))
        mode = data.get("mode")
        if isinstance(mode, str) and mode:
            tracker.mode = mode
        tracker.scored = int(data.get("scored") or 0)
        tracker.kept = int(data.get("kept") or 0)
        tracker.dropped_low_score = int(data.get("dropped_low_score") or 0)
        tracker.dropped_near_dup = int(data.get("dropped_near_dup") or 0)
        tracker.errors = int(data.get("errors") or 0)
        tracker.candidates_low_score = int(data.get("candidates_low_score") or 0)
        tracker.candidates_near_dup = int(data.get("candidates_near_dup") or 0)
        tracker.drop_near_dups = bool(data.get("drop_near_dups", tracker.drop_near_dups))
        tracker.min_score = data.get("min_score", tracker.min_score)
        top = data.get("top_dup_families") or []
        if isinstance(top, list):
            tracker.top_dup_snapshot = [dict(entry) for entry in top if isinstance(entry, dict)]
        return tracker


class InlineQCController:
    """Wrap scorer and gating logic used by inline QC."""

    def __init__(
        self,
        *,
        config,
        stats=None,
        scorer: QualityScorer,
        logger,
        enforce_drops: bool = True,
    ) -> None:
        """Initialize the controller with scorer and configuration.

        Args:
            config: QC configuration object.
            stats: Pipeline stats object containing a QC tracker.
            scorer (QualityScorer): Scorer used to evaluate records.
            logger: Logger for warning and error messages.
            enforce_drops (bool): Whether to drop records based on QC results.
        """
        self.cfg = config
        self.stats = stats
        self.scorer = scorer
        self.logger = logger
        self.enforce_drops = enforce_drops
        self.summary = QCSummaryTracker()
        self.reset(stats)

    @property
    def tracker(self) -> QCSummaryTracker:
        return self.summary

    def reset(self, stats: Any | None = None) -> None:
        """Reset internal tracker state and reattach to stats when provided."""

        if stats is not None:
            self.stats = stats
        tracker = QCSummaryTracker(
            enabled=True,
            mode=self.cfg.mode,
            min_score=self.cfg.min_score,
            drop_near_dups=bool(self.cfg.drop_near_dups),
        )
        self.summary = tracker
        target_stats = self.stats
        if target_stats is not None:
            try:
                target_stats.qc = tracker  # type: ignore[attr-defined]
            except Exception:
                pass

    def accept(self, record: Record) -> bool:
        """Score a record and apply QC gating rules.

        Args:
            record (Record): Record to score.

        Returns:
            bool: True if the record passes QC and should be kept.
        """
        return self.process_record(record) is not None

    def on_record(self, record: Record) -> Record:
        """No-op observer hook; returns the record unchanged."""
        # Inline QC performs all work inside accept(); observer hook is a pass-through.
        return record

    def process_record(self, record: Record) -> Record | None:
        """Score and optionally drop a record, merging QC metadata when kept."""

        try:
            qc_result = self.scorer.score_record(record)
        except Exception as exc:
            self.summary.record_error()
            if getattr(self.cfg, "fail_on_error", False):
                raise
            if self.logger:
                path = best_effort_record_path(record)
                self.logger.warning(
                    "QC scoring failed for %s (mode=%s): %s",
                    path,
                    getattr(self.cfg, "mode", None),
                    exc,
                )
            return None if self.enforce_drops else self._mark_qc_error(record)

        try:
            keep = self.summary.observe(qc_result, apply_gates=self.enforce_drops)
            self._merge_qc_meta_impl(record, qc_result)
        except Exception as exc:
            self.summary.record_error()
            if getattr(self.cfg, "fail_on_error", False):
                raise
            if self.logger:
                path = best_effort_record_path(record)
                self.logger.warning("QC post-processing failed for %s: %s", path, exc)
            return None if self.enforce_drops else self._mark_qc_error(record)
        if not keep:
            return None
        return record

    def summary_dict(self) -> Dict[str, Any]:
        """Return the current QC summary as a dictionary."""
        return self.summary.as_dict()

    def _merge_qc_meta_impl(self, record: Record, qc_result: Dict[str, Any]) -> None:
        """Attach QC-derived metadata to the record meta dictionary."""

        if not isinstance(record, dict):
            return
        meta = ensure_meta_dict(record)
        tokens_est = qc_result.get("tokens")
        if tokens_est is not None:
            meta["approx_tokens"] = tokens_est
            meta.setdefault("tokens", tokens_est)
        canonical_qc, qc_signals = filter_qc_meta(qc_result)
        merge_meta_defaults(record, canonical_qc)
        extra = meta.get("extra")
        if not isinstance(extra, dict):
            extra = {}
            meta["extra"] = extra
        qc_extra = extra.get("qc_signals")
        if not isinstance(qc_extra, dict):
            qc_extra = {}
            extra["qc_signals"] = qc_extra
        for key, value in qc_signals.items():
            if key in qc_extra:
                continue
            qc_extra[key] = value

    def _mark_qc_error(self, record: Record) -> Record:
        """Annotate a record when QC processing fails but we keep the record."""
        if isinstance(record, dict):
            meta = ensure_meta_dict(record)
            meta["qc_error"] = True
        return record


class InlineQCHook(RunLifecycleHook):
    """Lifecycle hook that applies inline QC gating and summaries."""

    def __init__(self, controller: InlineQCController, *, write_csv: bool = False, csv_suffix: str | None = None) -> None:
        self._controller = controller
        self._write_csv = write_csv
        self._csv_suffix = csv_suffix

    def on_run_start(self, ctx: RunContext) -> None:
        self._controller.reset(ctx.stats)

    def on_record(self, record: Record) -> Record | None:
        try:
            return self._controller.process_record(record)
        except Exception:
            self._controller.tracker.record_error()
            log.warning("Inline QC hook failed", exc_info=True)
            if self._controller.enforce_drops:
                return None
            return self._controller._mark_qc_error(record)

    def on_run_end(self, ctx: RunContext) -> None:
        ctx.stats.qc = self._controller.tracker
        if not self._write_csv:
            return
        try:
            self._write_csv_report(ctx)
        except Exception:  # pragma: no cover - best-effort logging
            log.warning("Failed to write inline QC CSV", exc_info=True)

    def on_artifacts(self, artifacts: RunArtifacts, ctx: RunContext) -> None:
        # Inline QC doesn't need run-level artifacts; all work is handled via
        # per-record processing and run-end summary propagation.
        return None

    def _write_csv_report(self, ctx: RunContext) -> None:
        jsonl_path = ctx.cfg.sinks.primary_jsonl_name or ctx.cfg.metadata.primary_jsonl
        if not jsonl_path:
            return
        out_csv = _derive_csv_path(jsonl_path, self._csv_suffix)
        if not out_csv:
            return
        scorer = getattr(self._controller, "scorer", None)
        if scorer is None:
            return

        reset = getattr(scorer, "reset_state", None)
        if callable(reset):
            try:
                reset()
            except Exception:
                log.debug("QC scorer reset_state failed; continuing", exc_info=True)

        tracker = QCSummaryTracker(
            enabled=True,
            mode=self._controller.cfg.mode,
            min_score=self._controller.cfg.min_score,
            drop_near_dups=bool(self._controller.cfg.drop_near_dups),
        )
        from .qc_post import collect_qc_rows_from_jsonl, emit_qc_csv

        rows = collect_qc_rows_from_jsonl(
            str(jsonl_path),
            qc_cfg=self._controller.cfg,
            config=ctx.cfg,
            scorer=scorer,
            runtime=getattr(ctx, "runtime", None),
            executor_hint=None,
            tracker=tracker,
        )
        emit_qc_csv(rows, str(jsonl_path), out_csv)

        err_count = tracker.errors
        if err_count and hasattr(ctx, "stats"):
            try:
                ctx.stats.qc.errors += err_count  # type: ignore[attr-defined]
            except Exception:
                pass
            log.warning("Inline QC CSV scoring for %s skipped %s lines", jsonl_path, err_count)


def _derive_csv_path(jsonl_path: Optional[str], suffix: Optional[str]) -> Optional[str]:
    """Derive a QC CSV path from a primary JSONL path and suffix."""

    if not jsonl_path:
        return None
    if suffix and ((os.sep and os.sep in suffix) or (os.altsep and os.altsep in suffix)):
        return suffix
    suffix = suffix or "_quality.csv"
    base = str(jsonl_path)
    if base.endswith(".jsonl"):
        base = base[:-6]
    return base + suffix


def summarize_qc_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    mode: str,
    min_score: Optional[float],
    drop_near_dups: bool,
    apply_gates: bool = False,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Build a summary dictionary from QC rows for post-processing mode.

    Args:
        rows (Iterable[dict[str, Any]]): QC rows to aggregate.
        mode (str): QC mode label to store.
        min_score (float | None): Minimum score threshold.
        drop_near_dups (bool): Whether to drop near duplicates.
        apply_gates (bool): Whether to apply gating during aggregation.
        enabled (bool): Whether QC was enabled for the run.

    Returns:
        dict[str, Any]: Summary produced by QCSummaryTracker.as_dict().
    """
    tracker = QCSummaryTracker(
        enabled=enabled,
        mode=mode,
        min_score=min_score,
        drop_near_dups=drop_near_dups,
    )
    for row in rows:
        tracker.observe(row, apply_gates=apply_gates)
    return tracker.as_dict()
