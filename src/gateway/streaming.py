"""SSE 스트리밍 유틸리티."""

import asyncio
import json
from typing import AsyncIterator


async def multiplex_sse(
    *generators: AsyncIterator,
) -> AsyncIterator[dict]:
    """여러 비동기 제너레이터를 SSE 이벤트로 멀티플렉싱."""
    queue: asyncio.Queue = asyncio.Queue()

    async def feed(gen: AsyncIterator, source: str):
        async for item in gen:
            await queue.put({"source": source, **item})
        await queue.put(None)

    tasks = [
        asyncio.create_task(feed(gen, f"source_{i}"))
        for i, gen in enumerate(generators)
    ]

    done_count = 0
    while done_count < len(tasks):
        item = await queue.get()
        if item is None:
            done_count += 1
        else:
            yield item

    for task in tasks:
        if not task.done():
            task.cancel()
