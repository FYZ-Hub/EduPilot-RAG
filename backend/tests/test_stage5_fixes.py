"""阶段 5 独立修复的回归测试（BUG-5-01 / BUG-5-02 及已确认边界）。

全部离线运行：只使用 httpx MockTransport 与注入替身，不访问网络、
不下载模型、不依赖 GPU。
"""

from __future__ import annotations

import json

import httpx
import pytest

from tests.conftest import build_settings
from app.core.errors import ApiError
from app.documents.fingerprint import pipeline_fingerprint, stage_fingerprints
from app.embedding.api import ApiEmbeddingProvider
from app.embedding.base import descriptor_for as embedding_descriptor_for
from app.rerank.api import ApiReranker
from app.rerank.base import descriptor_for as rerank_descriptor_for
from app.rerank.factory import build_rerank_provider
from app.search.reranking import RERANK_MAX_CANDIDATES, RerankingRetriever
from app.search.types import HybridResult, RetrievalDiagnostics, RetrievedChunk

# --- 个人信息测试数据（全部为明显虚构值） ------------------------------------

NAME_LINE = "姓名：测试学生甲"
STUDENT_ID = "TEST-2026-0001"
STUDENT_ID_LINE = f"学号：{STUDENT_ID}"
EMAIL = "test.student@example.invalid"
PHONE = "13800138000"
ID_CARD = "110101200001010010"

PII_QUERY = (
    f"{NAME_LINE} {STUDENT_ID_LINE} 邮箱 {EMAIL} 手机 {PHONE} 身份证 {ID_CARD} 毕业学分要求是多少"
)
PII_DOCUMENT = f"学生联系信息：{NAME_LINE}，{STUDENT_ID_LINE}，邮箱 {EMAIL}，电话 {PHONE}"

PII_VALUES = (NAME_LINE, STUDENT_ID, EMAIL, PHONE, ID_CARD, "测试学生甲")


# --- 通用替身 ----------------------------------------------------------------


def _capture_requests(monkeypatch, module_path: str, response_factory):
    """把模块内 ``httpx.Client`` 换成 MockTransport 客户端并记录外发 JSON。"""
    captured: list[dict] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured.append(payload)
        return httpx.Response(200, json=response_factory(payload))

    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(f"{module_path}.httpx.Client", factory)
    return captured


def _api_rerank_settings(tmp_path, **overrides):
    values = {
        "rerank_provider": "api",
        "rerank_base_url": "https://invalid.example.invalid/v1",
        "rerank_api_key": "test-key-not-real",
        "rerank_model": "rerank-test-model",
    }
    values.update(overrides)
    return build_settings(tmp_path, **values)


def _api_embedding_settings(tmp_path, **overrides):
    values = {
        "embedding_provider": "api",
        "embedding_base_url": "https://invalid.example.invalid/v1",
        "embedding_api_key": "test-key-not-real",
        "embedding_model": "embedding-test-model",
    }
    values.update(overrides)
    return build_settings(tmp_path, **values)


def _rerank_response(payload: dict) -> dict:
    return {
        "results": [
            {"index": index, "relevance_score": 0.5}
            for index in range(len(payload["documents"]))
        ]
    }


def _embedding_response(payload: dict) -> dict:
    return {"data": [{"embedding": [0.1] * 1024} for _ in payload["input"]]}


def _chunk(chunk_id: str, **overrides) -> RetrievedChunk:
    values = {
        "chunk_id": chunk_id,
        "doc_id": "doc",
        "text": f"text-{chunk_id}",
        "file_name": "a.pdf",
        "file_type": "pdf",
        "doc_category": "academic_policy",
        "source_type": "demo",
        "source_key": "k",
        "fused_score": 1.0,
    }
    values.update(overrides)
    return RetrievedChunk(**values)


def _hybrid_results(count: int) -> list[HybridResult]:
    return [
        HybridResult(chunk=_chunk(f"c{index:02d}", fused_score=1.0), fused_score=1.0)
        for index in range(count)
    ]


class _FakeHybrid:
    results: list = []

    def __init__(self, *args, **kwargs):
        pass

    def search(self, query, filters=None, top_k=None):
        return list(type(self).results), RetrievalDiagnostics()


class _StubReranker:
    """只返回分数的测试替身。"""

    def __init__(self, fn, descriptor):
        self._fn = fn
        self.descriptor = descriptor
        self.calls: list = []

    def rerank(self, query, candidates):
        self.calls.append({"query": query, "candidates": list(candidates)})
        return self._fn(query, list(candidates))

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_fake_hybrid():
    _FakeHybrid.results = []
    yield
    _FakeHybrid.results = []


def _retriever(settings, context, worker, reranker):
    session = context.session_factory()
    return RerankingRetriever(
        session, settings, worker.vectors, worker.embeddings, reranker, worker.coordinator
    ), session


# --- BUG-5-02：外部模型 payload 未移除个人信息 ---------------------------------


def test_api_reranker_scrubs_personal_information_before_send(tmp_path, monkeypatch) -> None:
    captured = _capture_requests(monkeypatch, "app.rerank.api", _rerank_response)
    provider = ApiReranker(_api_rerank_settings(tmp_path))

    provider.rerank(PII_QUERY, [PII_DOCUMENT, "普通候选文本"])

    assert captured, "必须真实构造外发请求"
    payload = captured[0]
    serialized = json.dumps(payload, ensure_ascii=False)
    for leaked in PII_VALUES:
        assert leaked not in serialized, f"外发 payload 仍包含个人信息：{leaked}"

    from app.core.privacy import (
        REDACTED_EMAIL,
        REDACTED_ID,
        REDACTED_NAME,
        REDACTED_PHONE,
        REDACTED_STUDENT_ID,
    )

    for placeholder in (
        REDACTED_NAME,
        REDACTED_STUDENT_ID,
        REDACTED_EMAIL,
        REDACTED_PHONE,
        REDACTED_ID,
    ):
        assert placeholder in serialized


def test_api_embedding_scrubs_personal_information_before_send(tmp_path, monkeypatch) -> None:
    captured = _capture_requests(monkeypatch, "app.embedding.api", _embedding_response)
    provider = ApiEmbeddingProvider(_api_embedding_settings(tmp_path))

    provider.embed_documents([PII_QUERY, PII_DOCUMENT])

    assert captured
    serialized = json.dumps(captured[0], ensure_ascii=False)
    for leaked in PII_VALUES:
        assert leaked not in serialized, f"外发 payload 仍包含个人信息：{leaked}"

    from app.core.privacy import REDACTED_EMAIL, REDACTED_NAME

    assert REDACTED_NAME in serialized
    assert REDACTED_EMAIL in serialized


def test_api_reranker_does_not_mutate_caller_candidates(tmp_path, monkeypatch) -> None:
    _capture_requests(monkeypatch, "app.rerank.api", _rerank_response)
    provider = ApiReranker(_api_rerank_settings(tmp_path))
    candidates = [PII_DOCUMENT, "普通候选文本"]
    snapshot = list(candidates)

    provider.rerank(PII_QUERY, candidates)

    # 只清洗外发副本：调用方持有的原始文本不得被改写
    assert candidates == snapshot


def test_privacy_scrub_is_deterministic_and_idempotent() -> None:
    from app.core.privacy import scrub

    once = scrub(PII_QUERY)
    assert once == scrub(PII_QUERY)
    assert scrub(once) == once
    assert scrub("") == ""
    assert scrub("无个人信息的一段普通中文文本") == "无个人信息的一段普通中文文本"


def test_privacy_scrub_covers_required_categories() -> None:
    from app.core.privacy import (
        REDACTED_EMAIL,
        REDACTED_ID,
        REDACTED_NAME,
        REDACTED_PHONE,
        REDACTED_STUDENT_ID,
        scrub,
    )

    assert REDACTED_NAME in scrub(NAME_LINE)
    assert REDACTED_NAME in scrub("学生姓名：测试学生乙")
    assert REDACTED_STUDENT_ID in scrub(STUDENT_ID_LINE)
    assert REDACTED_EMAIL in scrub(f"联系 {EMAIL} 即可")
    assert REDACTED_PHONE in scrub(f"电话 {PHONE}")
    assert REDACTED_ID in scrub(f"身份证 {ID_CARD}")
    assert REDACTED_STUDENT_ID in scrub("个人编号：P-0001")

    assert NAME_LINE not in scrub(PII_QUERY)
    assert STUDENT_ID not in scrub(PII_QUERY)
    assert EMAIL not in scrub(PII_QUERY)
    assert PHONE not in scrub(PII_QUERY)
    assert ID_CARD not in scrub(PII_QUERY)


def test_privacy_scrub_keeps_course_codes_and_plain_numbers() -> None:
    from app.core.privacy import scrub

    # 课程代码不是个人信息：不得被误清洗
    assert scrub("课程编号：QM-CS201") == "课程编号：QM-CS201"
    assert scrub("学分为 3，共 160 学分") == "学分为 3，共 160 学分"
    assert scrub("学期 2026-2027-1") == "学期 2026-2027-1"


# --- 隐私版本与指纹边界 -------------------------------------------------------


def test_api_embedding_fingerprint_tracks_privacy_version(tmp_path) -> None:
    from app.core.privacy import PRIVACY_POLICY_VERSION
    from app.embedding.base import EmbeddingDescriptor

    settings = _api_embedding_settings(tmp_path)
    descriptor = embedding_descriptor_for(settings)
    assert descriptor.privacy_policy_version == PRIVACY_POLICY_VERSION
    assert PRIVACY_POLICY_VERSION in json.dumps(descriptor.as_dict())

    # 不含隐私版本的旧式描述符指纹必须不同：切换清洗版本即触发 API 索引重建
    baseline = EmbeddingDescriptor(
        provider="api",
        model=settings.embedding_model,
        revision="api",
        dimension=settings.embedding_dimension,
    )
    assert descriptor.fingerprint != baseline.fingerprint


def test_fake_and_local_embedding_fingerprint_is_privacy_agnostic(tmp_path) -> None:
    from app.core.privacy import PRIVACY_POLICY_VERSION

    fake = embedding_descriptor_for(build_settings(tmp_path, embedding_provider="fake"))
    local = embedding_descriptor_for(build_settings(tmp_path, embedding_provider="local"))
    for descriptor in (fake, local):
        assert descriptor.privacy_policy_version is None
        assert "privacy_policy_version" not in descriptor.as_dict()
        assert PRIVACY_POLICY_VERSION not in json.dumps(descriptor.as_dict())


def test_reranker_fingerprint_tracks_privacy_version(tmp_path) -> None:
    from app.core.privacy import PRIVACY_POLICY_VERSION

    descriptor = rerank_descriptor_for(build_settings(tmp_path, rerank_provider="fake"))
    assert descriptor.privacy_policy_version == PRIVACY_POLICY_VERSION
    assert PRIVACY_POLICY_VERSION in json.dumps(descriptor.as_dict())


def test_privacy_version_does_not_enter_document_pipeline_fingerprint(tmp_path) -> None:
    base = build_settings(tmp_path, embedding_provider="fake")
    rerank_changed = build_settings(
        tmp_path,
        embedding_provider="fake",
        rerank_provider="local",
        rerank_model="BAAI/bge-reranker-v2-m3",
        rerank_revision="deadbeef",
        rerank_device="cuda",
        rerank_batch_size=4,
    )
    assert pipeline_fingerprint(base) == pipeline_fingerprint(rerank_changed)
    assert stage_fingerprints(base) == stage_fingerprints(rerank_changed)


# --- Top 6 硬上限 ------------------------------------------------------------


def test_rerank_output_never_exceeds_six_with_large_top_k(ingest_demo, search) -> None:
    ingest_demo()
    results, diagnostics = search.rerank("学分认定", top_k=999)
    assert len(results) <= 6
    assert diagnostics.reranked_candidates == len(results)


def test_rerank_allows_fewer_than_six(ingest_demo, search) -> None:
    ingest_demo()
    results, _diagnostics = search.rerank("学分认定", top_k=3)
    assert 0 < len(results) <= 3


def test_rerank_top_k_zero_or_negative_returns_empty(ingest_demo, search) -> None:
    ingest_demo()
    for value in (0, -1):
        results, diagnostics = search.rerank("学分认定", top_k=value)
        assert results == []
        assert diagnostics.reranked_candidates == 0


def test_configured_top_k_cannot_exceed_hard_cap(context, worker, tmp_path, monkeypatch) -> None:
    settings = build_settings(
        tmp_path, rerank_provider="fake", rerank_top_k=50, worker_enabled=False
    )
    _FakeHybrid.results = _hybrid_results(10)
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _StubReranker(lambda q, c: [1.0] * len(c), rerank_descriptor_for(settings))

    retriever, session = _retriever(settings, context, worker, stub)
    try:
        results, _diagnostics = retriever.search("学分认定")
    finally:
        session.close()

    assert len(results) == 6


def test_rerank_rrf_input_is_still_capped_at_twenty(context, worker, tmp_path, monkeypatch) -> None:
    settings = build_settings(tmp_path, rerank_provider="fake", worker_enabled=False)
    _FakeHybrid.results = _hybrid_results(40)
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _StubReranker(lambda q, c: [1.0] * len(c), rerank_descriptor_for(settings))

    retriever, session = _retriever(settings, context, worker, stub)
    try:
        _results, diagnostics = retriever.search("学分认定")
    finally:
        session.close()

    assert len(stub.calls[0]["candidates"]) == RERANK_MAX_CANDIDATES
    assert diagnostics.rerank_input_candidates == RERANK_MAX_CANDIDATES


# --- API 配置校验 ------------------------------------------------------------


def test_api_reranker_requires_api_key(tmp_path) -> None:
    settings = _api_rerank_settings(tmp_path, rerank_api_key="")
    provider = ApiReranker(settings)
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", ["候选"])
    assert error.value.code == "RERANK_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "api_rerank_not_configured"


def test_api_embedding_requires_api_key(tmp_path) -> None:
    settings = _api_embedding_settings(tmp_path, embedding_api_key="")
    provider = ApiEmbeddingProvider(settings)
    with pytest.raises(ApiError) as error:
        provider.embed_documents(["文本"])
    assert error.value.code == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "api_embedding_not_configured"


def test_api_missing_config_does_not_leak_values(tmp_path) -> None:
    secret = "sk-should-never-appear-12345"
    settings = _api_rerank_settings(
        tmp_path, rerank_api_key=secret, rerank_base_url="", rerank_model=""
    )
    provider = ApiReranker(settings)
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", ["候选"])
    haystack = f"{error.value.message}{error.value.details}{error.value!s}{error.value!r}"
    assert secret not in haystack
    assert "invalid.example.invalid" not in haystack


# --- API readiness 语义 ------------------------------------------------------


def test_api_reranker_becomes_ready_only_after_success(tmp_path, monkeypatch) -> None:
    _capture_requests(monkeypatch, "app.rerank.api", _rerank_response)
    provider = ApiReranker(_api_rerank_settings(tmp_path))
    assert provider.loaded is False

    provider.rerank("学分认定", ["候选甲", "候选乙"])
    assert provider.loaded is True


def test_api_reranker_readiness_resets_on_failure_and_close(tmp_path, monkeypatch) -> None:
    _capture_requests(monkeypatch, "app.rerank.api", _rerank_response)
    provider = ApiReranker(_api_rerank_settings(tmp_path))
    provider.rerank("学分认定", ["候选甲"])
    assert provider.loaded is True

    provider.close()
    assert provider.loaded is False

    # 成功一次后，被替换为失败响应时必须重新回落为 false
    provider.rerank("学分认定", ["候选甲"])
    assert provider.loaded is True
    monkeypatch.setattr(
        "app.rerank.api.httpx.Client", _raising_client(RuntimeError("upstream down"))
    )
    with pytest.raises(ApiError):
        provider.rerank("学分认定", ["候选甲"])
    assert provider.loaded is False


def test_api_embedding_readiness_is_evidence_based(tmp_path, monkeypatch) -> None:
    """与 ApiReranker 一致的严格语义：只有真实成功过一次才算 ready。"""
    _capture_requests(monkeypatch, "app.embedding.api", _embedding_response)
    provider = ApiEmbeddingProvider(_api_embedding_settings(tmp_path))
    assert provider.loaded is False

    provider.embed_documents(["一段普通文本"])
    assert provider.loaded is True

    provider.close()
    assert provider.loaded is False


def _raising_client(error: Exception):
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(error)
            ),
            **kwargs,
        )

    return factory


# --- 计时 --------------------------------------------------------------------


def test_rerank_elapsed_ms_only_counts_reranker_stage(
    ingest_demo, context, worker, tmp_path, monkeypatch
) -> None:
    ingest_demo()
    from app.search import hybrid as hybrid_module
    from app.search import reranking as reranking_module

    clock = {"value": 100.0}
    real_search = hybrid_module.HybridRetriever.search

    def slow_search(self, query, filters=None, top_k=None):
        clock["value"] += 5.0  # Hybrid 阶段推进 5 秒（必须不计入 rerank_elapsed_ms）
        return real_search(self, query, filters, top_k)

    monkeypatch.setattr(hybrid_module.HybridRetriever, "search", slow_search)
    monkeypatch.setattr(reranking_module, "perf_counter", lambda: clock["value"], raising=False)

    settings = build_settings(tmp_path, rerank_provider="fake", worker_enabled=False)

    def _scoring(query, candidates):
        clock["value"] += 0.25  # Reranker 阶段推进 0.25 秒
        return [0.5] * len(candidates)

    stub = _StubReranker(_scoring, rerank_descriptor_for(settings))
    retriever, session = _retriever(settings, context, worker, stub)
    try:
        _results, diagnostics = retriever.search("学分认定")
    finally:
        session.close()

    assert diagnostics.rerank_elapsed_ms == 250
    # Hybrid 时间仍然保留在检索诊断里
    assert diagnostics.retrieval.elapsed_ms >= 0


# ============================================================================
# 阶段 5 最终边界修复：BUG-5-03（隐私清洗覆盖）/ BUG-5-04（空输入 readiness）
# ============================================================================

# 常见键值格式：空格 / 冒号 + 内部空格 / 表格竖线 / TAB
NAME_SAMPLES = [
    ("姓名 张三", "姓名：[REDACTED_NAME]"),
    ("姓名：张 三", "姓名：[REDACTED_NAME]"),
    ("| 姓名 | 张三 |", "| 姓名：[REDACTED_NAME] |"),
    ("姓名\t张三", "姓名：[REDACTED_NAME]"),
]
STUDENT_ID_SAMPLES = [
    ("学号 20260001", "学号：[REDACTED_STUDENT_ID]"),
    ("| 学号 | 20260001 |", "| 学号：[REDACTED_STUDENT_ID] |"),
]
LANDLINE_SAMPLES = [
    ("电话 010-12345678", "电话：[REDACTED_PHONE]"),
    ("电话：(010) 12345678", "电话：[REDACTED_PHONE]"),
]
FORMAT_SAMPLES = NAME_SAMPLES + STUDENT_ID_SAMPLES + LANDLINE_SAMPLES

# 学术术语：绝不能被误清洗
ACADEMIC_SAFE_TEXT = (
    "课程编号：QM-CS201 课程名称：数据结构 学分：3 "
    "学期：2026-2027-1 开课日期：2026-09-01 总学分 160 已修 120"
)


def _forbid_http(monkeypatch, module_path: str) -> dict:
    """替换 httpx.Client：记录构造与请求次数，任何真实 POST 都直接失败。"""
    probe = {"constructed": 0, "posted": 0}

    class _NoHttpClient:
        def __init__(self, *args, **kwargs):
            probe["constructed"] += 1

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def post(self, *args, **kwargs):
            probe["posted"] += 1
            raise AssertionError("不得在没有真实输入时发出 HTTP 请求")

    monkeypatch.setattr(f"{module_path}.httpx.Client", _NoHttpClient)
    return probe


def test_privacy_scrub_covers_common_key_value_formats() -> None:
    from app.core.privacy import scrub

    for raw, expected in FORMAT_SAMPLES:
        assert scrub(raw) == expected, f"未按预期清洗：{raw!r}"


def test_privacy_scrub_removes_leaked_identifiers_in_all_formats() -> None:
    from app.core.privacy import scrub

    for raw, _expected in NAME_SAMPLES:
        assert "张三" not in scrub(raw)
    for raw, _expected in STUDENT_ID_SAMPLES:
        assert "20260001" not in scrub(raw)
    for raw, _expected in LANDLINE_SAMPLES:
        assert "12345678" not in scrub(raw)


def test_privacy_scrub_is_idempotent_for_new_formats() -> None:
    from app.core.privacy import scrub

    for raw, _expected in FORMAT_SAMPLES:
        once = scrub(raw)
        assert scrub(once) == once


def test_privacy_scrub_does_not_over_redact_academic_terms() -> None:
    from app.core.privacy import scrub

    assert scrub(ACADEMIC_SAFE_TEXT) == ACADEMIC_SAFE_TEXT


def test_privacy_v2_bumps_only_api_embedding_fingerprint(tmp_path) -> None:
    from app.core.privacy import PRIVACY_POLICY_VERSION
    from app.embedding.base import EmbeddingDescriptor

    api = embedding_descriptor_for(_api_embedding_settings(tmp_path))
    assert api.privacy_policy_version == PRIVACY_POLICY_VERSION
    v1_baseline = EmbeddingDescriptor(
        provider="api",
        model=api.model,
        revision="api",
        dimension=api.dimension,
        privacy_policy_version="external-privacy-v1",
    )
    assert api.fingerprint != v1_baseline.fingerprint

    # Local / Fake 不含该字段，指纹与 pipeline_fingerprint 不受影响
    for provider in ("fake", "local"):
        descriptor = embedding_descriptor_for(build_settings(tmp_path, embedding_provider=provider))
        assert descriptor.privacy_policy_version is None
        assert "privacy_policy_version" not in descriptor.as_dict()


def test_api_reranker_scrubs_new_formats_before_send(tmp_path, monkeypatch) -> None:
    captured = _capture_requests(monkeypatch, "app.rerank.api", _rerank_response)
    provider = ApiReranker(_api_rerank_settings(tmp_path))

    query = "；".join(raw for raw, _ in FORMAT_SAMPLES)
    documents = [raw for raw, _ in FORMAT_SAMPLES]
    snapshot = list(documents)

    provider.rerank(query, documents)

    serialized = json.dumps(captured[0], ensure_ascii=False)
    for leaked in ("张三", "20260001", "12345678", "010-12345678"):
        assert leaked not in serialized, f"外发 payload 仍包含个人信息：{leaked}"
    assert captured[0]["query"] != query
    assert documents == snapshot, "调用方持有的候选文本不得被改写"


def test_api_embedding_scrubs_new_formats_before_send(tmp_path, monkeypatch) -> None:
    captured = _capture_requests(monkeypatch, "app.embedding.api", _embedding_response)
    provider = ApiEmbeddingProvider(_api_embedding_settings(tmp_path))

    texts = [raw for raw, _ in FORMAT_SAMPLES]
    snapshot = list(texts)
    provider.embed_documents(texts)

    serialized = json.dumps(captured[0], ensure_ascii=False)
    for leaked in ("张三", "20260001", "12345678", "010-12345678"):
        assert leaked not in serialized, f"外发 payload 仍包含个人信息：{leaked}"
    assert texts == snapshot, "调用方持有的文本不得被改写"


# --- BUG-5-04：空 Embedding 输入伪造 readiness --------------------------------


def test_api_embedding_empty_input_returns_empty_without_http(tmp_path, monkeypatch) -> None:
    probe = _forbid_http(monkeypatch, "app.embedding.api")
    provider = ApiEmbeddingProvider(_api_embedding_settings(tmp_path))
    assert provider.loaded is False

    assert provider.embed_documents([]) == []
    assert probe == {"constructed": 0, "posted": 0}, "空输入不得创建 HTTP Client 或发出请求"
    assert provider.loaded is False, "空输入不得把 readiness 伪造为 true"


def test_api_embedding_empty_input_must_not_flip_readiness(tmp_path) -> None:
    """原始错误行为：loaded=False → embed_documents([])=[] → loaded=True（无任何请求）。"""
    provider = ApiEmbeddingProvider(_api_embedding_settings(tmp_path))
    loaded_before = provider.loaded
    result = provider.embed_documents([])
    loaded_after = provider.loaded

    assert (loaded_before, result, loaded_after) == (False, [], False)


def test_api_embedding_empty_input_keeps_existing_success(tmp_path, monkeypatch) -> None:
    _capture_requests(monkeypatch, "app.embedding.api", _embedding_response)
    provider = ApiEmbeddingProvider(_api_embedding_settings(tmp_path))

    provider.embed_documents(["一段普通文本"])
    assert provider.loaded is True

    # 空输入是 no-op：不得凭空清除既有的成功证据
    assert provider.embed_documents([]) == []
    assert provider.loaded is True


def test_api_embedding_becomes_ready_only_after_real_validated_response(
    tmp_path, monkeypatch
) -> None:
    captured = _capture_requests(monkeypatch, "app.embedding.api", _embedding_response)
    provider = ApiEmbeddingProvider(_api_embedding_settings(tmp_path))

    assert provider.loaded is False
    provider.embed_documents(["文本"])
    assert len(captured) == 1, "必须真的发出一次请求"
    assert provider.loaded is True

    # 失败响应（结构非法）必须回落为 false
    monkeypatch.setattr(
        "app.embedding.api.httpx.Client",
        _raising_client(RuntimeError("upstream down")),
    )
    with pytest.raises(ApiError):
        provider.embed_documents(["文本"])
    assert provider.loaded is False

    provider.close()
    assert provider.loaded is False


def test_api_reranker_empty_candidates_remain_a_no_op(tmp_path, monkeypatch) -> None:
    _capture_requests(monkeypatch, "app.rerank.api", _rerank_response)
    provider = ApiReranker(_api_rerank_settings(tmp_path))
    provider.rerank("查询", ["候选甲"])
    assert provider.loaded is True

    probe = _forbid_http(monkeypatch, "app.rerank.api")

    assert provider.rerank("查询", []) == []
    assert probe == {"constructed": 0, "posted": 0}, "空候选不得创建 HTTP Client 或发出请求"
    # 空候选是 no-op：不清除既有成功证据，也不伪造新的
    assert provider.loaded is True


def test_api_reranker_empty_candidates_on_fresh_provider(tmp_path, monkeypatch) -> None:
    probe = _forbid_http(monkeypatch, "app.rerank.api")
    provider = ApiReranker(_api_rerank_settings(tmp_path))

    assert provider.rerank("查询", []) == []
    assert probe == {"constructed": 0, "posted": 0}
    assert provider.loaded is False


# ============================================================================
# BUG-5-05：隐私清洗误删同一行的学术字段
# ============================================================================

MIXED_NAME_LINE = "姓名 张三 课程编号 QM-CS201 学分 3 学期 2026-2027-1"
MIXED_NAME_EXPECTED = "姓名：[REDACTED_NAME] 课程编号 QM-CS201 学分 3 学期 2026-2027-1"

MIXED_SPACED_NAME_LINE = "姓名：张 三 课程名称：数据结构 学分：3"
MIXED_SPACED_NAME_EXPECTED = "姓名：[REDACTED_NAME] 课程名称：数据结构 学分：3"

MIXED_STUDENT_ID_LINE = "学号 20260001 课程代码 QM-CS201 成绩 88"
MIXED_STUDENT_ID_EXPECTED = "学号：[REDACTED_STUDENT_ID] 课程代码 QM-CS201 成绩 88"

ACADEMIC_KEEP_TOKENS = ("QM-CS201", "数据结构", "学分", "学期", "成绩", "课程编号", "课程代码", "课程名称")


def test_privacy_scrub_preserves_academic_fields_after_name() -> None:
    from app.core.privacy import REDACTED_NAME, scrub

    result = scrub(MIXED_NAME_LINE)
    assert "张三" not in result
    assert REDACTED_NAME in result
    assert result == MIXED_NAME_EXPECTED


def test_privacy_scrub_preserves_academic_fields_after_spaced_name() -> None:
    from app.core.privacy import REDACTED_NAME, scrub

    result = scrub(MIXED_SPACED_NAME_LINE)
    assert "张 三" not in result
    assert REDACTED_NAME in result
    assert result == MIXED_SPACED_NAME_EXPECTED


def test_privacy_scrub_preserves_academic_fields_after_student_id() -> None:
    from app.core.privacy import REDACTED_STUDENT_ID, scrub

    result = scrub(MIXED_STUDENT_ID_LINE)
    assert "20260001" not in result
    assert REDACTED_STUDENT_ID in result
    assert result == MIXED_STUDENT_ID_EXPECTED


def test_privacy_scrub_stops_at_common_field_boundaries() -> None:
    """PII 值必须在下一个常见字段开始前停止，且不得吞掉字段间空白。"""
    from app.core.privacy import REDACTED_NAME, REDACTED_STUDENT_ID, scrub

    cases = [
        ("姓名 李四 专业 计算机科学与技术", "姓名：[REDACTED_NAME] 专业 计算机科学与技术"),
        ("姓名 李四 年级 2026", "姓名：[REDACTED_NAME] 年级 2026"),
        ("姓名 李四 日期 2026-09-01", "姓名：[REDACTED_NAME] 日期 2026-09-01"),
        ("姓名 李四 时间 08:00", "姓名：[REDACTED_NAME] 时间 08:00"),
        ("姓名 李四 地点 教一楼", "姓名：[REDACTED_NAME] 地点 教一楼"),
        ("姓名 李四 状态 已通过", "姓名：[REDACTED_NAME] 状态 已通过"),
        ("姓名 李四 课程类别 专业必修", "姓名：[REDACTED_NAME] 课程类别 专业必修"),
        ("姓名 李四 备注：无", "姓名：[REDACTED_NAME] 备注：无"),
        ("姓名 李四 培养层次：本科", "姓名：[REDACTED_NAME] 培养层次：本科"),
        ("学号 20260001 成绩 88", "学号：[REDACTED_STUDENT_ID] 成绩 88"),
    ]
    for raw, expected in cases:
        assert scrub(raw) == expected, f"字段边界未生效：{raw!r}"


def test_privacy_scrub_keeps_academic_tokens_in_mixed_lines() -> None:
    from app.core.privacy import scrub

    for raw in (MIXED_NAME_LINE, MIXED_SPACED_NAME_LINE, MIXED_STUDENT_ID_LINE):
        result = scrub(raw)
        for token in ACADEMIC_KEEP_TOKENS:
            if token in raw:
                assert token in result, f"{token!r} 被误删：{raw!r}"


def test_redacted_value_length_is_bounded_by_a_constant() -> None:
    """字段值匹配必须有真实总长度上限，不能靠无界重复伪装成有界。"""
    from app.core.privacy import MAX_FIELD_VALUE_CHARS, scrub

    placeholder = "姓名：[REDACTED_NAME]"
    for size in (60, 400, 3000):
        raw = "姓名 " + "阿" * size
        result = scrub(raw)
        assert result.startswith(placeholder)
        removed = size - (len(result) - len(placeholder))
        assert 0 < removed <= MAX_FIELD_VALUE_CHARS


def test_privacy_scrub_is_idempotent_for_mixed_academic_lines() -> None:
    from app.core.privacy import scrub

    for raw in (MIXED_NAME_LINE, MIXED_SPACED_NAME_LINE, MIXED_STUDENT_ID_LINE):
        once = scrub(raw)
        assert scrub(once) == once


def test_privacy_policy_version_is_v3() -> None:
    from app.core.privacy import PRIVACY_POLICY_VERSION

    assert PRIVACY_POLICY_VERSION == "external-privacy-v3"


def test_privacy_v3_bumps_only_api_embedding_fingerprint(tmp_path) -> None:
    from app.core.privacy import PRIVACY_POLICY_VERSION
    from app.embedding.base import EmbeddingDescriptor

    api = embedding_descriptor_for(_api_embedding_settings(tmp_path))
    assert api.privacy_policy_version == PRIVACY_POLICY_VERSION
    v2_baseline = EmbeddingDescriptor(
        provider="api",
        model=api.model,
        revision="api",
        dimension=api.dimension,
        privacy_policy_version="external-privacy-v2",
    )
    assert api.fingerprint != v2_baseline.fingerprint
    # 与 v1 也不同：任一版本变化都会改变 API Embedding 指纹
    v1_baseline = EmbeddingDescriptor(
        provider="api",
        model=api.model,
        revision="api",
        dimension=api.dimension,
        privacy_policy_version="external-privacy-v1",
    )
    assert api.fingerprint != v1_baseline.fingerprint

    for provider in ("fake", "local"):
        descriptor = embedding_descriptor_for(build_settings(tmp_path, embedding_provider=provider))
        assert descriptor.privacy_policy_version is None
        assert "privacy_policy_version" not in descriptor.as_dict()


def test_api_reranker_preserves_academic_context_before_send(tmp_path, monkeypatch) -> None:
    captured = _capture_requests(monkeypatch, "app.rerank.api", _rerank_response)
    provider = ApiReranker(_api_rerank_settings(tmp_path))

    documents = [MIXED_STUDENT_ID_LINE, MIXED_SPACED_NAME_LINE]
    snapshot = list(documents)
    provider.rerank(MIXED_NAME_LINE, documents)

    serialized = json.dumps(captured[0], ensure_ascii=False)
    for leaked in ("张三", "张 三", "20260001"):
        assert leaked not in serialized, f"外发 payload 仍包含个人信息：{leaked}"
    assert "[REDACTED_NAME]" in serialized
    assert "[REDACTED_STUDENT_ID]" in serialized
    for token in ACADEMIC_KEEP_TOKENS:
        assert token in serialized, f"外发 payload 丢失学术字段：{token}"
    assert captured[0]["query"] == MIXED_NAME_EXPECTED
    assert captured[0]["documents"] == [MIXED_STUDENT_ID_EXPECTED, MIXED_SPACED_NAME_EXPECTED]
    assert documents == snapshot, "调用方持有的候选文本不得被改写"


def test_api_embedding_preserves_academic_context_before_send(tmp_path, monkeypatch) -> None:
    captured = _capture_requests(monkeypatch, "app.embedding.api", _embedding_response)
    provider = ApiEmbeddingProvider(_api_embedding_settings(tmp_path))

    texts = [MIXED_NAME_LINE, MIXED_STUDENT_ID_LINE, MIXED_SPACED_NAME_LINE]
    snapshot = list(texts)
    provider.embed_documents(texts)

    serialized = json.dumps(captured[0], ensure_ascii=False)
    for leaked in ("张三", "张 三", "20260001"):
        assert leaked not in serialized, f"外发 payload 仍包含个人信息：{leaked}"
    assert "[REDACTED_NAME]" in serialized
    assert "[REDACTED_STUDENT_ID]" in serialized
    for token in ACADEMIC_KEEP_TOKENS:
        assert token in serialized, f"外发 payload 丢失学术字段：{token}"
    assert captured[0]["input"] == [
        MIXED_NAME_EXPECTED,
        MIXED_STUDENT_ID_EXPECTED,
        MIXED_SPACED_NAME_EXPECTED,
    ]
    assert texts == snapshot, "调用方持有的文本不得被改写"
