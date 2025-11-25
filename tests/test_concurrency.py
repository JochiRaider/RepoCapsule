import pytest

from repocapsule.core.concurrency import Executor, ExecutorConfig, process_items_parallel


def test_map_unordered_submit_error_handled_and_continues():
    cfg = ExecutorConfig(max_workers=2, window=2, kind="thread")
    executor = Executor(cfg)

    items = [1, 2, 3]
    seen = []
    submit_errors = []

    def fn(x):
        return x * 2

    def on_result(res):
        seen.append(res)

    def on_submit_error(item, exc):
        submit_errors.append((item, exc))

    # Monkeypatch submit to fail for item == 2
    original_make = executor._make_executor

    class SubmitFailingExecutor:
        def __init__(self, real):
            self.pool = real

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.pool.__exit__(exc_type, exc, tb)  # type: ignore[attr-defined]

        def submit(self, fn_, item):
            if item == 2:
                raise RuntimeError("submit fail")
            return self.pool.submit(fn_, item)

    executor._make_executor = lambda: SubmitFailingExecutor(original_make())  # type: ignore[assignment]

    executor.map_unordered(
        items,
        fn,
        on_result,
        fail_fast=False,
        on_submit_error=on_submit_error,
    )

    assert set(seen) == {2, 6}  # 2 was skipped due to submit failure
    assert len(submit_errors) == 1
    assert submit_errors[0][0] == 2


def test_process_items_parallel_delegates_submit_and_worker_errors():
    items = [1, 2, 3]
    results = []
    submit_errors = []
    worker_errors = []

    def process_one(x):
        if x == 3:
            raise ValueError("boom")
        return (x, [x])

    def write_records(item, recs):
        results.append((item, list(recs)))

    def on_submit_error(item, exc):
        submit_errors.append(item)

    def on_worker_error(exc):
        worker_errors.append(str(exc))

    process_items_parallel(
        items,
        process_one,
        write_records,
        max_workers=2,
        window=2,
        fail_fast=False,
        on_submit_error=on_submit_error,
        on_worker_error=on_worker_error,
    )

    assert results == [(1, [1]), (2, [2])]
    assert worker_errors == ["boom"]
    assert submit_errors == []
