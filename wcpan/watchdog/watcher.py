from __future__ import annotations

import asyncio
import time
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import AsyncExitStack
from functools import partial
from typing import Awaitable, Callable, Optional, Protocol, Set, TypeVar, Union

from .walker import ChangeEntry, Walker
from .filters import Filter, create_default_filter


T = TypeVar('T')


class Runner(Protocol):
    async def __call__(self, cb: Callable[..., T], *args, **kwargs) -> T:
        pass


class Watcher(object):
    """
    Creates a filesystem watcher.

    stop_event is an asyncio.Event object which gives the watcher a hint about
    when to stop the watching loop. If stop_event is None, the loop will not
    stop.

    filter_ is a Filter object, to filter out files and directories being
    watching. If filter_ is None, create_default_filter() will be used.
    """

    def __init__(self,
        stop_event: Optional[asyncio.Event] = None,
        filter_: Optional[Filter] = None,
        min_sleep: int = 50,
        normal_sleep: int = 400,
        debounce: int = 1600,
        executor: Optional[Executor] = None,
    ):
        self._stop_event = stop_event
        self._filter = filter_
        self._min_sleep = min_sleep
        self._normal_sleep = normal_sleep
        self._debounce = debounce
        self._executor = executor

    async def __aenter__(self) -> WatcherCreator:
        async with AsyncExitStack() as stack:
            if self._executor is None:
                self._executor = stack.enter_context(ThreadPoolExecutor())
            self._raii = stack.pop_all()
        return WatcherCreator(self)

    async def __aexit__(self, type_, exc, tb):
        await self._raii.aclose()

    async def _run(self, cb: Callable[..., T], *args, **kwargs):
        fn = partial(cb, *args, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    async def _sleep(self, sec: float):
        await asyncio.sleep(sec)


class WatcherCreator(object):

    def __init__(self, context: Watcher) -> None:
        self._context = context

    def __call__(self,
        path: str,
        stop_event: Optional[asyncio.Event] = None,
        filter_: Optional[Filter] = None,
        min_sleep: Optional[int] = None,
        normal_sleep: Optional[int] = None,
        debounce: Optional[int] = None,
    ) -> ChangeIterator:
        if stop_event is None:
            stop_event = self._context._stop_event
        if filter_ is None:
            filter_ = create_default_filter()
        if min_sleep is None:
            min_sleep = self._context._min_sleep
        if normal_sleep is None:
            normal_sleep = self._context._normal_sleep
        if debounce is None:
            debounce = self._context._debounce

        return ChangeIterator(
            run=self._context._run,
            sleep=self._context._sleep,
            path=path,
            stop_event=stop_event,
            filter_=filter_,
            min_sleep=min_sleep,
            normal_sleep=normal_sleep,
            debounce=debounce,
        )


class ChangeIterator(object):

    def __init__(self,
        run: Runner,
        sleep: Callable[[float], Awaitable[None]],
        path: str,
        stop_event: Optional[asyncio.Event],
        filter_: Filter,
        min_sleep: int,
        normal_sleep: int,
        debounce: int,
    ):
        self._run = run
        self._sleep = sleep
        self._path = path
        self._stop_event = stop_event
        self._filter = filter_
        self._min_sleep = min_sleep
        self._normal_sleep = normal_sleep
        self._debounce = debounce

        self._walker: Union[Walker, None] = None

    def __aiter__(self):
        return self

    async def __anext__(self) -> Set[ChangeEntry]:
        if not self._walker:
            self._walker = Walker(self._filter, self._path)
            await self._run(self._walker)

        check_time = 0
        changes: Set[ChangeEntry] = set()
        last_change = 0
        while True:
            if self._stop_event and self._stop_event.is_set():
                raise StopAsyncIteration

            if not changes:
                last_change = unix_ms()

            if check_time:
                if changes:
                    sleep_time = self._min_sleep
                else:
                    sleep_time = max(self._normal_sleep - check_time, self._min_sleep)
                await self._sleep(sleep_time / 1000)

            s = unix_ms()
            new_changes = await self._run(self._walker)
            changes.update(new_changes)

            now = unix_ms()
            check_time = now - s
            debounced = now - last_change

            if changes and (not new_changes or debounced > self._debounce):
                return changes


def unix_ms():
    return int(round(time.time() * 1000))
