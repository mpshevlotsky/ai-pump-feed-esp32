# aioble.pyi

from typing import AsyncIterator, Any

class ScanResult:
    device: Any
    rssi: int
    def name(self) -> str: ...

async def scan(
        duration_ms: int,
        interval_us: int = ...,
        window_us: int = ...,
        active: bool = ...
) -> AsyncIterator[ScanResult]: ...
