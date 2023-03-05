from __future__ import annotations


__all__ = ("WatcherContext", "Watcher")


import asyncio
import os
import time
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import AsyncExitStack
from functools import partial
from typing import Awaitable, Callable, Protocol, TypeVar

from .walker import ChangeEntry, Walker
from .filters import Filter, create_default_filter


T = TypeVar("T")


class Runner(Protocol[T]):
    async def __call__(self, cb: Callable[..., T], *args, **kwargs) -> T:
        ...


class WatcherContext(object):
    """Context manager of watchers.

    This class maintains the context needed by watchers, especially executor.
    Parameters will be used as default values for each watchers.

    stop_event is an asyncio.Event object which gives the watcher a hint about
    when to stop the watching loop. If stop_event is None, the loop will NEVER
    stop.

    filter_ is a Filter object, to filter out files and directories being
    watching. If omitted, create_default_filter() will be used.

    sleep_sec is the time in second to wait for new changes coming.
    min_sleep_sec is the minimum time in second to wait for new changes coming.
    debounce_sec is the maximum time to collect changes.

    executor is an Executor object, used to walk through the file system. If
    omitted, a ThreadPoolExecutor will be used. If you supplied an Executor,
    then it is caller's responsibility to stop the Executor.
    """

    def __init__(
        self,
        *,
        stop_event: asyncio.Event | None = None,
        filter_: Filter | None = None,
        sleep_sec: float = 0.4,
        min_sleep_sec: float = 0.05,
        debounce_sec: float = 1.6,
        executor: Executor | None = None,
    ):
        self._stop_event = stop_event
        self._filter = filter_
        self._sleep_sec = sleep_sec
        self._min_sleep_sec = min_sleep_sec
        self._debounce_sec = debounce_sec
        self._executor = executor

    async def __aenter__(self) -> Watcher:
        async with AsyncExitStack() as stack:
            if self._executor is None:
                self._executor = stack.enter_context(ThreadPoolExecutor())
            self._raii = stack.pop_all()
        return Watcher(self)

    async def __aexit__(self, type_, exc, tb):
        await self._raii.aclose()

    async def _run(self, cb: Callable[..., T], *args, **kwargs) -> T:
        fn = partial(cb, *args, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    async def _sleep(self, sec: float):
        await asyncio.sleep(sec)


class Watcher(object):
    def __init__(self, context: WatcherContext) -> None:
        self._context = context

    def __call__(
        self,
        path: os.PathLike | str,
        *,
        stop_event: asyncio.Event | None = None,
        filter_: Filter | None = None,
        sleep_sec: float | None = None,
        min_sleep_sec: float | None = None,
        debounce_sec: float | None = None,
    ) -> ChangeIterator:
        if not isinstance(path, str):
            path = str(path)
        if stop_event is None:
            stop_event = self._context._stop_event
        if filter_ is None:
            filter_ = create_default_filter()
        if min_sleep_sec is None:
            min_sleep_sec = self._context._min_sleep_sec
        if sleep_sec is None:
            sleep_sec = self._context._sleep_sec
        if debounce_sec is None:
            debounce_sec = self._context._debounce_sec

        return ChangeIterator(
            run=self._context._run,
            sleep=self._context._sleep,
            path=path,
            stop_event=stop_event,
            filter_=filter_,
            sleep_sec=sleep_sec,
            min_sleep_sec=min_sleep_sec,
            debounce_sec=debounce_sec,
        )


class ChangeIterator(object):
    def __init__(
        self,
        *,
        run: Runner,
        sleep: Callable[[float], Awaitable[None]],
        path: str,
        stop_event: asyncio.Event | None,
        filter_: Filter,
        sleep_sec: float,
        min_sleep_sec: float,
        debounce_sec: float,
    ):
        self._run = run
        self._sleep = sleep
        self._path = path
        self._stop_event = stop_event
        self._filter = filter_
        self._sleep_sec = sleep_sec
        self._min_sleep_sec = min_sleep_sec
        self._debounce_sec = debounce_sec
        self._walker: Walker | None = None

    def __aiter__(self):
        return self

    async def __anext__(self) -> set[ChangeEntry]:
        # Setup the waler, and run it to setup the snapshot.
        if not self._walker:
            self._walker = Walker(self._filter, self._path)
            await self._run(self._walker)

        # Changes gathered in this iteration.
        changes: set[ChangeEntry] = set()
        # The time interval the walker used.
        last_check_took = 0.0
        # The timestamp where the changes begin. Used to calculate debouncing.
        last_change = 0.0
        while True:
            # Check stop_event, this is the ONLY way to stop the iteration.
            if self._stop_event and self._stop_event.is_set():
                raise StopAsyncIteration

            # Nothing changed yet, update the timestamp.
            if not changes:
                last_change = now_in_sec()

            # We have to sleep awhile after we have checked last time.
            if last_check_took > 0.0:
                if changes:
                    # Likely to have more changes, sleep shorter.
                    sleep_time = self._min_sleep_sec
                else:
                    # Likely to be idle, sleep longer.
                    sleep_time = max(
                        self._sleep_sec - last_check_took,
                        self._min_sleep_sec,
                    )
                await self._sleep(sleep_time)

            # Gathering changes.
            time_before_walk = now_in_sec()
            new_changes = await self._run(self._walker)
            changes.update(new_changes)

            # Update timestamps.
            time_after_walk = now_in_sec()
            last_check_took = time_after_walk - time_before_walk
            debounced = time_after_walk - last_change

            # We end this iteration if we have got any changes, and one of the
            # following condition has meet:
            # 1. There is no new changes to gather.
            # 2. It exceeds debouncing time.
            if changes and (not new_changes or debounced > self._debounce_sec):
                return changes


def now_in_sec():
    return time.time()
