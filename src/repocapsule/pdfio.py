# pipeline.py
# SPDX-License-Identifier: MIT

from __future__ import annotations

from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, FIRST_COMPLETED, wait, Future
from dataclasses import dataclass, field
from typing import Optional, Iterable, Sequence, Dict, List, Tuple, Any, Callable, TypeVar
from pathlib import Path

from .config import RepocapsuleConfig
from .interfaces import Source, Sink, RepoContext, Record
from .convert import iter_records_from_bytes
from .log import get_logger
from .qc_utils import update_dup_family_counts, top_dup_families
from .qc_controller import InlineQCController


log = get_logger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class _WorkItem:
    item: Any
    ctx: Optional[RepoContext]


@dataclass
class _ProcessFileCallable:
    config: RepocapsuleConfig
    materialize: bool = False

    def __call__(self, work: _WorkItem) -> Tuple[Any, Iterable[Record]]:
        item = work.item
        ctx = work.ctx
        rel = getattr(item, "path", None) or getattr(item, "rel_path", None)
        data = getattr(item, "data", None)
        if rel is None or data is None:
            raise ValueError("FileItem missing 'path' or 'data'")
        recs_iter = iter_records_from_bytes(
            data,
            rel,
            config=self.config,
            context=ctx,
        )
        if self.materialize:
            recs_iter = list(recs_iter)
        return item, recs_iter

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class PipelineStats:
    files: int = 0
    bytes: int = 0
    records: int = 0
    sink_errors: int = 0
    source_errors: int = 0
    by_ext: Dict[str, int] = field(default_factory=dict)
    qc_scored: int = 0
    qc_kept: int = 0
    qc_dropped_low_score: int = 0
    qc_dropped_near_dup: int = 0
    qc_errors: int = 0
    qc_candidates_low_score: int = 0
    qc_candidates_near_dup: int = 0
    qc_enabled: bool = False
    qc_mode: str = "inline"
    qc_dup_families: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        # Keep a simple, stable shape for external reporting/JSONL footers.
        data: Dict[str, object] = {
            "files": int(self.files),
            "bytes": int(self.bytes),
            "records": int(self.records),
            "sink_errors": int(self.sink_errors),
            "source_errors": int(self.source_errors),
            "by_ext": dict(self.by_ext),
        }
        data["qc"] = {
            "enabled": bool(self.qc_enabled),
            "mode": self.qc_mode,
            "scored": int(self.qc_scored),
            "kept": int(self.qc_kept),
            "dropped_low_score": int(self.qc_dropped_low_score),
            "dropped_near_dup": int(self.qc_dropped_near_dup),
            "errors": int(self.qc_errors),
            "candidates_low_score": int(self.qc_candidates_low_score),
            "candidates_near_dup": int(self.qc_candidates_near_dup),
            "top_dup_families": top_dup_families(self.qc_dup_families),
        }
        return data

    def record_dup_family(self, family_id: Optional[str], path: Optional[str]) -> None:
        update_dup_family_counts(self.qc_dup_families, family_id, path)

    def qc_top_dup_families(self) -> List[Dict[str, Any]]:
        return top_dup_families(self.qc_dup_families)



def process_items_parallel(
    items: Iterable[T],
    process_one: Callable[[T], Tuple[Any, Iterable[Record]]],
    write_records: Callable[[Any, Iterable[Record]], None],
    *,
    max_workers: int,
    window: int,
    fail_fast: bool,
    on_submit_error: Optional[Callable[[T, BaseException], None]] = None,
    on_worker_error: Optional[Callable[[BaseException], None]] = None,
    executor_kind: str = "thread",
) -> None:
    """
    Execute work items in a bounded ThreadPoolExecutor and stream results.

    Parameters
    ----------
    items:
        Iterable of work items. Items are consumed lazily, so callers can feed
        an unbounded generator (e.g., streaming file sources).
    process_one:
        Callable executed in worker threads. Must return (item, iterable).
    write_records:
        Callable invoked in the main thread with each completed result.
    max_workers:
        ThreadPoolExecutor worker count (must be >= 1).
    window:
        Bounded in-flight submission count. Acts as backpressure so sources do
        not outrun processing. Automatically clamped to >= max_workers.
    fail_fast:
        If True, re-raise errors from submission/worker execution immediately.
    on_submit_error / on_worker_error:
        Optional callbacks for bookkeeping/logging when failures occur.
    """
    if max_workers < 1:
        raise ValueError("process_items_parallel requires max_workers >= 1")
    window = max(window, max_workers)
    kind = (executor_kind or "thread").strip().lower()
    if kind == "process":
        executor_cls = ProcessPoolExecutor
    else:
        executor_cls = ThreadPoolExecutor
        kind = "thread"
    log.debug("process_items_parallel using %s executor (max_workers=%d)", kind, max_workers)
    with executor_cls(max_workers=max_workers) as pool:
        pending: List[Future[Tuple[Any, Iterable[Record]]]] = []

        def _drain(block: bool = False) -> None:
            nonlocal pending
            if not pending:
                return
            done, still = wait(
                pending,
                timeout=None if block else 0.0,
                return_when=FIRST_COMPLETED,
            )
            pending = list(still)
            for fut in done:
                try:
                    item, recs = fut.result()
                except Exception as exc:
                    if on_worker_error:
                        on_worker_error(exc)
                    if fail_fast:
                        raise
                    continue
                write_records(item, recs)

        for work in items:
            try:
                pending.append(pool.submit(process_one, work))
            except Exception as exc:
                if on_submit_error:
                    on_submit_error(work, exc)
                if fail_fast:
                    raise
                continue
            if len(pending) >= window:
                _drain(block=True)

        while pending:
            _drain(block=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_source_with_stack(stack: ExitStack, src: Source) -> Source:
    """Enter context manager if supported; else register close() if present."""
    enter = getattr(src, "__enter__", None)
    if callable(enter):
        return stack.enter_context(src)  
    close = getattr(src, "close", None)
    if callable(close):
        stack.callback(close)  
    return src

def _prepare_sinks(stack: ExitStack, sinks: Sequence[Sink], ctx: Optional[RepoContext]) -> List[Sink]:
    """
    Open sinks exactly once with RepoContext if supported.
    """
    open_sinks: List[Sink] = []
    for s in sinks:
        try:
            # Call explicit open(context) if available.
            open_fn = getattr(s, "open", None)
            if callable(open_fn):
                open_fn(ctx)  
            # Ensure we close at the end if close exists.
            close_fn = getattr(s, "close", None)
            if callable(close_fn):
                stack.callback(close_fn)  
            open_sinks.append(s)
        except Exception as e:
            log.warning("Sink %s failed to open: %s", getattr(s, "__class__", type(s)).__name__, e)
    return open_sinks


def _get_context_from_source(source: Source) -> Optional[RepoContext]:
    return getattr(source, "context", None)  


def _ext_key(path: str) -> str:
    try:
        return Path(path).suffix.lower()
    except Exception:
        return ""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_pipeline(*, config: RepocapsuleConfig) -> Dict[str, int]:
    """Run the end-to-end pipeline described by ``config``."""
    cfg = config
    stats = PipelineStats()
    qc_cfg = cfg.qc
    stats.qc_enabled = bool(qc_cfg.enabled)
    stats.qc_mode = qc_cfg.mode
    advisory_mode = qc_cfg.mode == "advisory"
    qc_active = bool(qc_cfg.enabled and qc_cfg.mode == "inline" and getattr(qc_cfg, "scorer", None))
    if qc_cfg.enabled and qc_cfg.mode == "inline" and not qc_active:
        log.warning("QC enabled but no scorer is configured; skipping inline annotations.")
    qc_controller: Optional[InlineQCController] = None
    if qc_active:
        qc_controller = InlineQCController(
            config=qc_cfg,
            stats=stats,
            scorer=qc_cfg.scorer,  # type: ignore[arg-type]
            logger=log,
            enforce_drops=not advisory_mode,
        )

    with ExitStack() as stack:
        open_sources: List[Source] = [_open_source_with_stack(stack, src) for src in cfg.sources.sources]
        initial_ctx: Optional[RepoContext] = cfg.sinks.context
        open_sinks: List[Sink] = _prepare_sinks(stack, cfg.sinks.sinks, initial_ctx)
        if not open_sinks:
            log.warning("No sinks are open; processed records will be dropped.")

        materialize_results = (cfg.pipeline.executor_kind or "thread").strip().lower() == "process"
        processor = _ProcessFileCallable(config=cfg, materialize=materialize_results)

        def _increment_file_stats(item: Any) -> None:
            size = getattr(item, "size", None)
            if size is None:
                data = getattr(item, "data", b"")
                size = len(data) if isinstance(data, (bytes, bytearray)) else 0
            stats.files += 1
            stats.bytes += int(size or 0)
            ext = _ext_key(getattr(item, "path", ""))
            stats.by_ext[ext] = stats.by_ext.get(ext, 0) + 1

        def _write_records(item: Any, recs: Iterable[Record]) -> None:
            for record in recs:
                if qc_controller and not qc_controller.should_keep(record):
                    continue
                wrote_any = False
                for sink in open_sinks:
                    try:
                        sink.write(record)  # type: ignore[attr-defined]
                        wrote_any = True
                    except Exception as exc:  # sink failure should not stop pipeline
                        log.warning(
                            "Sink %s failed to write record for %s: %s",
                            getattr(sink, "__class__", type(sink)).__name__,
                            getattr(item, "path", "<unknown>"),
                            exc,
                        )
                        stats.sink_errors += 1
                if wrote_any:
                    stats.records += 1

        def _process_serial(item: Any, ctx: Optional[RepoContext]) -> None:
            try:
                _, recs = processor(_WorkItem(item=item, ctx=ctx))
                _write_records(item, recs)
            except Exception as exc:
                log.warning(
                    "Processing failed for %s: %s",
                    getattr(item, "path", "<unknown>"),
                    exc,
                )
                stats.source_errors += 1
                if cfg.pipeline.fail_fast:
                    raise

        def _iter_source_items() -> Iterable[_WorkItem]:
            for source in open_sources:
                ctx = getattr(source, "context", cfg.sinks.context)
                try:
                    for item in source.iter_files():
                        yield _WorkItem(item=item, ctx=ctx)
                except Exception as exc:
                    log.warning(
                        "Source %s failed while iterating files: %s",
                        getattr(source, "__class__", type(source)).__name__,
                        exc,
                    )
                    stats.source_errors += 1
                    if cfg.pipeline.fail_fast:
                        raise

        if cfg.pipeline.max_workers <= 1:
            for work in _iter_source_items():
                _increment_file_stats(work.item)
                _process_serial(work.item, work.ctx)
        else:
            window = cfg.pipeline.submit_window or (4 * cfg.pipeline.max_workers)

            def _work_iter() -> Iterable[_WorkItem]:
                for work in _iter_source_items():
                    _increment_file_stats(work.item)
                    yield work

            def _worker(work: _WorkItem) -> Tuple[Any, Iterable[Record]]:
                return processor(work)

            def _on_submit_error(work: _WorkItem, exc: BaseException) -> None:
                item = work.item
                log.warning("Scheduling failed for %s: %s", getattr(item, "path", "<unknown>"), exc)
                stats.source_errors += 1

            def _on_worker_error(exc: BaseException) -> None:
                log.warning("Worker failed: %s", exc)
                stats.source_errors += 1

            process_items_parallel(
                _work_iter(),
                _worker,
                _write_records,
                max_workers=cfg.pipeline.max_workers,
                window=window,
                fail_fast=cfg.pipeline.fail_fast,
                on_submit_error=_on_submit_error,
                on_worker_error=_on_worker_error,
                executor_kind=cfg.pipeline.executor_kind,
            )

    if qc_cfg.enabled:
        min_score_str = (
            f"{qc_cfg.min_score:.1f}" if qc_cfg.min_score is not None else "off"
        )
        log.info(
            "QC summary (min_score=%s, drop_near_dups=%s)\n"
            "  scored: %d\n"
            "  kept: %d\n"
            "  dropped_low_score: %d\n"
            "  dropped_near_dup: %d\n"
            "  candidates_low_score: %d\n"
            "  candidates_near_dup: %d\n"
            "  errors: %d",
            min_score_str,
            "on" if qc_cfg.drop_near_dups else "off",
            stats.qc_scored,
            stats.qc_kept,
            stats.qc_dropped_low_score,
            stats.qc_dropped_near_dup,
            stats.qc_candidates_low_score,
            stats.qc_candidates_near_dup,
            stats.qc_errors,
        )
        top = stats.qc_top_dup_families()
        if top:
            lines = [
                f"    - {entry['dup_family_id']}: count={entry['count']} examples={entry.get('examples', [])}"
                for entry in top
            ]
            log.info("Largest duplicate families:\n%s", "\n".join(lines))
        else:
            log.info("Largest duplicate families: none")

    return stats.as_dict()

__all__ = ["run_pipeline", "PipelineStats"]    
