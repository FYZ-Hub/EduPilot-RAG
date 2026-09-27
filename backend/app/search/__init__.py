"""检索层：FTS5 关键词、Chroma 向量与 RRF 混合检索。

本包**不做**便捷再导出：``app.documents.fingerprint`` 需要 ``app.search.schema``
作为叶子依赖，如果包初始化时提前导入 ``dense``/``hybrid`` 会形成循环导入。
请直接按完整路径导入，例如 ``from app.search.hybrid import HybridRetriever``。
"""
