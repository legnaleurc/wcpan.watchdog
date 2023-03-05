import asyncio
import argparse
import signal
import sys

from .watcher import WatcherContext
from .filters import create_default_filter, matches_glob


def main(args: list[str] | None = None) -> int:
    return asyncio.run(amain(args))


async def amain(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]

    kwargs, rest = parse_args(args)
    filter_ = create_filter(kwargs)
    is_quiet = kwargs.quiet

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, lambda: stop_event.set())

    async with ChildProcess(rest) as child, WatcherContext() as watcher:
        async for changes in watcher(
            kwargs.path, stop_event=stop_event, filter_=filter_
        ):
            if not is_quiet:
                sys.stderr.write(f"{str(changes)}\n")
            await child.restart()

    return 0


def parse_args(args: list[str]):
    parser = argparse.ArgumentParser("wcpan.watchdog")

    parser.add_argument("--quiet", "-q", action="store_true", default=False)
    parser.add_argument("--include", "-i", action="append")
    parser.add_argument("--exclude", "-e", action="append")
    parser.add_argument("path", nargs="?", type=str, default=".")

    rest: list[str] = []
    try:
        i = args.index("--")
        rest = args[i + 1 :]
        args = args[:i]
        kwargs = parser.parse_args(args)
    except ValueError:
        kwargs = parser.parse_args(args)

    return kwargs, rest


def create_filter(kwargs: argparse.Namespace):
    filter_ = create_default_filter()
    if kwargs.include:
        for p in kwargs.include:
            filter_.include(matches_glob(p))
    if kwargs.exclude:
        for p in kwargs.exclude:
            filter_.exclude(matches_glob(p))
    return filter_


class ChildProcess(object):
    def __init__(self, args: list[str]):
        self._args = args
        self._p: asyncio.subprocess.Process | None = None

    async def __aenter__(self):
        if not self._args:
            return self
        self._p = await spawn(self._args)
        return self

    async def __aexit__(self, type_, e, tb):
        if not self._p:
            return
        await kill(self._p)

    async def restart(self):
        if not self._p:
            return
        await kill(self._p)
        self._p = await spawn(self._args)


async def spawn(args: list[str]):
    p = await asyncio.create_subprocess_exec(*args)
    return p


async def kill(p: asyncio.subprocess.Process):
    if p.returncode is not None:
        return

    p.terminate()
    try:
        return await asyncio.wait_for(p.wait(), timeout=2)
    except asyncio.TimeoutError:
        pass

    p.kill()
    return await p.wait()
