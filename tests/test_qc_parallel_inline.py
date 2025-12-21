import threading
from collections.abc import Iterable

from sievio.core.builder import PipelinePlan, PipelineRuntime
from sievio.core.config import QCConfig, QCMode, SievioConfig
from sievio.core.interfaces import FileItem
from sievio.core.pipeline import PipelineEngine
from sievio.core.qc_controller import InlineQCHook


class _DummySource:
    def __init__(self, items: Iterable[FileItem]) -> None:
        self._items = list(items)

    def iter_files(self):
        yield from self._items


class _DummySink:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def open(self, context=None) -> None:
        return None

    def write(self, record) -> None:
        if isinstance(record, dict):
            self.records.append(record)

    def close(self) -> None:
        return None


class _DummyExtractor:
    def extract(self, item, *, config, context=None):
        data = item.data or b""
        text = data.decode("utf-8", "ignore")
        return [{"text": text, "meta": {"path": item.path}}]


def test_parallel_inline_qc_runs_in_worker_threads():
    seen: set[int] = set()
    lock = threading.Lock()
    main_id = threading.get_ident()

    class DummyScorer:
        def __init__(self, shared: set[int], lock_obj: threading.Lock) -> None:
            self._shared = shared
            self._lock = lock_obj

        def clone_for_parallel(self):
            return DummyScorer(self._shared, self._lock)

        def score_record(self, record):
            with self._lock:
                self._shared.add(threading.get_ident())
            return {"score": 100.0, "near_dup": False}

    qc_cfg = QCConfig(enabled=True, mode=QCMode.INLINE, parallel_inline=True, scorer=DummyScorer(seen, lock))
    cfg = SievioConfig(qc=qc_cfg)
    cfg.pipeline.executor_kind = "thread"
    cfg.pipeline.max_workers = 2

    items = [
        FileItem(path="a.txt", data=b"hello", size=5),
        FileItem(path="b.txt", data=b"world", size=5),
    ]
    runtime = PipelineRuntime(
        http_client=None,
        sources=(_DummySource(items),),
        sinks=(_DummySink(),),
        file_extractor=_DummyExtractor(),
        bytes_handlers=(),
        lifecycle_hooks=(InlineQCHook(qc_cfg=qc_cfg, scorer=qc_cfg.scorer),),
    )
    plan = PipelinePlan(spec=cfg, runtime=runtime)
    engine = PipelineEngine(plan)
    stats = engine.run()

    assert stats.records == 2
    assert seen
    assert main_id not in seen
    quality = stats.qc.get_screener("quality", create=False)
    assert quality is not None
    assert quality.scored == 2
