"""Entry point functions to execute `bytewax.Dataflow`s.

"""
from typing import Any, Callable, Iterable, List, Optional, Tuple, Union

from multiprocess import get_context

from bytewax.recovery import RecoveryConfig

from .bytewax import AdvanceTo, cluster_main, Dataflow, Emit, run_main


def run(
    flow: Dataflow,
    inp: Iterable[Tuple[int, Any]],
    *,
    recovery_config: Optional[RecoveryConfig] = None,
) -> List[Tuple[int, Any]]:
    """Pass data through a dataflow running in the current thread.

    Blocks until execution is complete.

    Output is collected into a list before returning, thus output must
    be finite.

    Handles distributing input and collecting output. You'd commonly
    use this for tests or prototyping in notebooks.

    >>> flow = Dataflow()
    >>> flow.map(str.upper)
    >>> flow.capture()
    >>> out = run(flow, [(0, "a"), (1, "b"), (2, "c")])
    >>> sorted(out)
    [(0, 'A'), (1, 'B'), (2, 'C')]

    Args:

        flow: Dataflow to run.

        inp: Input data. If you are recovering a stateful dataflow,
            your input should resume from the last finalized epoch.

        recovery_config: State recovery config. See
            `bytewax.recovery`. If `None`, state will not be
            persisted.

    Returns:

        List of `(epoch, item)` tuples seen by capture operators.

    """

    def input_builder(worker_index, worker_count, resume_epoch):
        assert resume_epoch == 0, "Recovery doesn't work with iterator based input"
        assert worker_index == 0
        for epoch, item in inp:
            yield AdvanceTo(epoch)
            yield Emit(item)

    out = []

    def output_builder(worker_index, worker_count):
        assert worker_index == 0
        return out.append

    run_main(
        flow,
        input_builder,
        output_builder,
        recovery_config=recovery_config,
    )

    return out


def _gen_addresses(proc_count: int) -> Iterable[str]:
    return [f"localhost:{proc_id + 2101}" for proc_id in range(proc_count)]


def spawn_cluster(
    flow: Dataflow,
    input_builder: Callable[[int, int, int], Iterable[Union[AdvanceTo, Emit]]],
    output_builder: Callable[[int, int, int], Callable[[Tuple[int, Any]], None]],
    *,
    recovery_config: Optional[RecoveryConfig] = None,
    proc_count: int = 1,
    worker_count_per_proc: int = 1,
    mp_ctx=get_context("spawn"),
) -> List[Tuple[int, Any]]:
    """Execute a dataflow as a cluster of processes on this machine.

    Blocks until execution is complete.

    Starts up cluster processes for you and handles connecting them
    together. You'd commonly use this for notebook analysis that needs
    parallelism and higher throughput, or simple stand-alone demo
    programs.

    >>> from bytewax.testing import doctest_ctx
    >>> flow = Dataflow()
    >>> flow.capture()
    >>> def input_builder(worker_index, worker_count, resume_epoch):
    ...   for epoch, item in enumerate(range(resume_epoch, 3)):
    ...     yield AdvanceTo(epoch)
    ...     yield Emit(item)
    >>> def output_builder(worker_index, worker_count):
    ...     return print
    >>> spawn_cluster(
    ...     flow,
    ...     input_builder,
    ...     output_builder,
    ...     proc_count=2,
    ...     mp_ctx=doctest_ctx,  # Outside a doctest, you'd skip this.
    ... )  # doctest: +ELLIPSIS
    (...)

    See `bytewax.run_main()` for a way to test input and output
    builders without the complexity of starting a cluster.

    See `bytewax.run_cluster()` for a convenience method to pass data
    through a dataflow for notebook development.

    See `bytewax.cluster_main()` for starting one process in a cluster
    in a distributed situation.

    Args:

        flow: Dataflow to run.

        input_builder: Yields `AdvanceTo()` or `Emit()` with this
            worker's input. Must resume from the epoch specified.

        output_builder: Returns a callback function for each worker
            thread, called with `(epoch, item)` whenever and item
            passes by a capture operator on this process.

        recovery_config: State recovery config. See
            `bytewax.recovery`. If `None`, state will not be
            persisted.

        proc_count: Number of processes to start.

        worker_count_per_proc: Number of worker threads to start on
            each process.

        mp_ctx: `multiprocessing` context to use. Use this to
            configure starting up subprocesses via spawn or
            fork. Defaults to spawn.

    """
    addresses = _gen_addresses(proc_count)
    with mp_ctx.Pool(processes=proc_count) as pool:
        futures = [
            pool.apply_async(
                cluster_main,
                (
                    flow,
                    input_builder,
                    output_builder,
                ),
                {
                    "recovery_config": recovery_config,
                    "addresses": addresses,
                    "proc_id": proc_id,
                    "worker_count_per_proc": worker_count_per_proc,
                },
            )
            for proc_id in range(proc_count)
        ]
        pool.close()

        for future in futures:
            # Will re-raise exceptions from subprocesses.
            future.get()

        pool.join()


def run_cluster(
    flow: Dataflow,
    inp: Iterable[Tuple[int, Any]],
    *,
    recovery_config: Optional[RecoveryConfig] = None,
    proc_count: int = 1,
    worker_count_per_proc: int = 1,
    mp_ctx=get_context("spawn"),
) -> List[Tuple[int, Any]]:
    """Pass data through a dataflow running as a cluster of processes on
    this machine.
    Blocks until execution is complete.

    Both input and output are collected into lists, thus both must be
    finite.

    Starts up cluster processes for you, handles connecting them
    together, distributing input, and collecting output. You'd
    commonly use this for notebook analysis that needs parallelism and
    higher throughput, or simple stand-alone demo programs.

    >>> from bytewax.testing import doctest_ctx
    >>> flow = Dataflow()
    >>> flow.map(str.upper)
    >>> flow.capture()
    >>> out = run_cluster(
    ...     flow,
    ...     [(0, "a"), (1, "b"), (2, "c")],
    ...     proc_count=2,
    ...     mp_ctx=doctest_ctx,  # Outside a doctest, you'd skip this.
    ... )
    >>> sorted(out)
    [(0, 'A'), (1, 'B'), (2, 'C')]

    See `bytewax.spawn_cluster()` for starting a cluster on this
    machine with full control over inputs and outputs.

    See `bytewax.cluster_main()` for starting one process in a cluster
    in a distributed situation.

    Args:

        flow: Dataflow to run.

        inp: Input data. Will be reified to a list before sending to
            processes. Will be partitioned between workers for you. If
            you are recovering a stateful dataflow, you must ensure
            your input resumes from the last finalized epoch.

        recovery_config: State recovery config. See
            `bytewax.recovery`. If `None`, state will not be
            persisted.

        proc_count: Number of processes to start.

        worker_count_per_proc: Number of worker threads to start on
            each process.

        mp_ctx: `multiprocessing` context to use. Use this to
            configure starting up subprocesses via spawn or
            fork. Defaults to spawn.

    Returns:

        List of `(epoch, item)` tuples seen by capture operators.
    """
    # A Manager starts up a background process to manage shared state.
    with mp_ctx.Manager() as man:
        inp = man.list(list(inp))

        def input_builder(worker_index, worker_count, resume_epoch):
            assert resume_epoch == 0, "Recovery doesn't work with iterator based input"
            for i, epoch_item in enumerate(inp):
                if i % worker_count == worker_index:
                    (epoch, item) = epoch_item
                    yield AdvanceTo(epoch)
                    yield Emit(item)

        out = man.list()

        def output_builder(worker_index, worker_count):
            return out.append

        spawn_cluster(
            flow,
            input_builder,
            output_builder,
            recovery_config=recovery_config,
            proc_count=proc_count,
            worker_count_per_proc=worker_count_per_proc,
            mp_ctx=mp_ctx,
        )

        # We have to copy out the shared state before process
        # shutdown.
        return list(out)
