# wcpan.watchdog

An asynchronous watchdog utility built with asyncio.

## Installation

```sh
pip install wcpan.watchdog
```

## Command Line Usage

You can simply use the main module like this:

```sh
python3 -m wcpan.watchdog -- <any command>
```

It will restart the command (if it is still running) for every file changed.

## Library Usage

You can also use this in your code:

```python
import asyncio
import signal

from wcpan.watchdog.watcher import Watcher

async def auto_rerun():
    path = 'the path you want to observe'

    # Setup how to stop the watcher.
    # In this example I use SIGINT to set the Event.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, lambda: stop_event.set())

    async with Watcher() as watcher:
        # This will not stop until stop_event is set.
        async for changes in watcher(path, stop_event=stop_event):
            # Changes contains changed information.
```
