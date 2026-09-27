"""显式关闭 Chroma 遥测。

Chroma 0.5 默认通过 PostHog 上报匿名事件。仅设置 ``anonymized_telemetry=False``
依赖第三方库内部开关，实测仍会构造客户端并尝试上报（在 posthog 新版本上直接抛错）。
因此这里提供一个不做任何事情的遥测实现，并通过 ``chroma_product_telemetry_impl``
让 Chroma 使用它：既不构造 PostHog 客户端，也不写用户 ID 文件，更不发送任何事件。
"""

from __future__ import annotations

from typing import Any

from overrides import override

from chromadb.telemetry.product import ProductTelemetryClient


class NoopTelemetry(ProductTelemetryClient):
    """采集入口直接空实现；不访问网络、不读写任何遥测缓存文件。"""

    @override
    def capture(self, event: Any) -> None:
        return None


__all__ = ["NoopTelemetry"]
