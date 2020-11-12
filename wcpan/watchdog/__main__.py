import asyncio
import sys

from .main import main

rv = main()
rv = asyncio.run(rv)
sys.exit(rv)
