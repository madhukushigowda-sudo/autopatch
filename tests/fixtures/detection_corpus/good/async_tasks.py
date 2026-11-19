"""Async helpers."""
import asyncio


async def fetch(n):
    await asyncio.sleep(0)
    return n * 2


async def gather_all(values):
    return await asyncio.gather(*(fetch(v) for v in values))


async def with_timeout(coro, seconds):
    try:
        return await asyncio.wait_for(coro, seconds)
    except asyncio.TimeoutError:
        return None
