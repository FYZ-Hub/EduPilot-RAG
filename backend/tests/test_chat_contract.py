"""阶段 6：请求契约与 SSE 公共契约测试（原始字节级别）。

覆盖：messages 条数与长度边界、role 与 filters 校验、``extra=forbid``、
422 JSON 含 request_id 且不回显正文、SSE framing（UTF-8 / 单行 JSON / 双换行）、
只允许四种事件、唯一终止事件、终止后无字节、响应头 request_id 与 done 一致。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.chat.schemas import (
    MAX_MESSAGES,
    MAX_MESSAGE_CHARS,
    MAX_TOTAL_CHARS,
)
from app.chat.sse import CITATION_FIELDS, SSE_EVENTS
from app.core.errors import LLM_PROVIDER_UNAVAILABLE
from app.core.request_id import REQUEST_ID_HEADER
from app.main import create_app
from tests.conftest import build_settings, parse_sse

CHAT_URL = "/api/chat/stream"


def _body(*messages: dict, **extra) -> dict:
    payload = {"messages": list(messages) or [{"role": "user", "content": "问题"}]}
    payload.update(extra)
    return payload


def _assert_single_terminal(body: bytes, events: list[tuple[str, dict]]) -> None:
    terminals = [name for name, _ in events if name in ("done", "error")]
    assert len(terminals) == 1, f"必须恰好一个终止事件，实际 {terminals}"
    assert events[-1][0] in ("done", "error"), "终止事件必须是最后一帧"
    assert body.endswith(b"\n\n"), "终止帧后不得再有字节"
    for name, _ in events:
        assert name in SSE_EVENTS, f"出现规范外事件：{name}"


# --- 请求边界 ---------------------------------------------------------------


@pytest.mark.parametrize("count", [1, MAX_MESSAGES])
def test_message_count_within_limit_is_accepted(client, count) -> None:
    messages = []
    for position in range(count):
        role = "user" if position % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"内容{position}"})
    if messages[-1]["role"] != "user":
        messages[-1] = {"role": "user", "content": "最后问题"}
    response = client.post(CHAT_URL, json={"messages": messages})
    # 环境未配置 LLM：开流前返回统一 JSON；关键是不因条数被 422 拒绝
    assert response.status_code != 422


@pytest.mark.parametrize("count", [0, MAX_MESSAGES + 1])
def test_message_count_outside_limit_is_rejected(client, count) -> None:
    messages = [{"role": "user", "content": "内容"} for _ in range(count)]
    response = client.post(CHAT_URL, json={"messages": messages})
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "system", "content": "越权角色"}],
        [{"role": "user", "content": "问题"}, {"role": "assistant", "content": "回答"}],
        [{"role": "user", "content": "   "}],
        [{"role": "user", "content": ""}],
    ],
)
def test_invalid_role_or_last_message_is_rejected(client, messages) -> None:
    response = client.post(CHAT_URL, json={"messages": messages})
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"


def test_last_user_message_must_be_non_empty(client) -> None:
    response = client.post(
        CHAT_URL,
        json={
            "messages": [
                {"role": "user", "content": "上一轮问题"},
                {"role": "assistant", "content": "上一轮回答"},
                {"role": "user", "content": "  "},
            ]
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("size", "accepted"),
    [(MAX_MESSAGE_CHARS, True), (MAX_MESSAGE_CHARS + 1, False)],
)
def test_single_message_length_boundary(client, size, accepted) -> None:
    response = client.post(CHAT_URL, json=_body({"role": "user", "content": "字" * size}))
    assert (response.status_code != 422) is accepted


def test_total_content_length_boundary(client) -> None:
    per_message = MAX_TOTAL_CHARS // MAX_MESSAGES
    messages = [
        {"role": "user", "content": "字" * per_message} for _ in range(MAX_MESSAGES - 1)
    ]
    rest = MAX_TOTAL_CHARS - per_message * (MAX_MESSAGES - 1)
    messages.append({"role": "user", "content": "字" * rest})
    assert sum(len(item["content"]) for item in messages) == MAX_TOTAL_CHARS
    assert client.post(CHAT_URL, json={"messages": messages}).status_code != 422

    messages[-1]["content"] += "字"
    assert sum(len(item["content"]) for item in messages) == MAX_TOTAL_CHARS + 1
    assert client.post(CHAT_URL, json={"messages": messages}).status_code == 422


def test_transcript_validation_does_not_echo_user_content(client) -> None:
    secret = "这句用户原文绝不能出现在错误里-12345"
    response = client.post(
        CHAT_URL,
        json={"messages": [{"role": "user", "content": secret}, {"role": "assistant", "content": "x"}]},
    )
    assert response.status_code == 422
    payload = response.json()
    assert secret not in response.text
    assert set(payload) == {"code", "message", "details", "request_id"}
    assert payload["request_id"]


@pytest.mark.parametrize(
    "filters",
    [
        {"major": 123},
        {"grade_year": "2026"},
        {"semester": ["2026-2027-1"]},
        {"doc_category": {"a": 1}},
        {"unknown": "x"},
        {"major": "计算机科学与技术", "extra": 1},
    ],
)
def test_filters_types_and_unknown_fields_are_rejected(client, filters) -> None:
    response = client.post(CHAT_URL, json=_body(filters=filters))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": [{"role": "user", "content": "问题"}], "unknown": 1},
        {"messages": [{"role": "user", "content": "问题", "extra": 1}]},
        {"messages": [{"role": "user", "content": "问题"}], "filters": {"major": None, "x": 1}},
    ],
)
def test_extra_fields_are_forbidden(client, payload) -> None:
    assert client.post(CHAT_URL, json=payload).status_code == 422


def test_valid_filters_are_accepted(client) -> None:
    response = client.post(
        CHAT_URL,
        json=_body(
            filters={
                "major": "计算机科学与技术",
                "grade_year": 2026,
                "semester": "2026-2027-1",
                "doc_category": "degree_plan",
            }
        ),
    )
    assert response.status_code != 422


# --- 开流前的已知错误 -------------------------------------------------------


def test_unconfigured_provider_returns_plain_json_before_stream(tmp_path) -> None:
    settings = build_settings(tmp_path, llm_provider="openai_compatible", llm_api_key="")
    application = create_app(settings)
    with TestClient(application) as client:
        response = client.post(CHAT_URL, json=_body())
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        payload = response.json()
        assert payload["code"] == LLM_PROVIDER_UNAVAILABLE
        assert payload["request_id"]
        assert response.headers[REQUEST_ID_HEADER] == payload["request_id"]
    application.state.context.engine.dispose()


# --- SSE framing ------------------------------------------------------------


def test_sse_framing_is_raw_utf8_single_line_json(ingest_demo, client) -> None:
    ingest_demo()
    response = client.post(CHAT_URL, json=_body({"role": "user", "content": "毕业总学分是多少？"}))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    body = response.content
    body.decode("utf-8")  # 必须可 UTF-8 解码
    assert b"event: " in body and b"data: " in body
    assert body.endswith(b"\n\n")

    events = parse_sse(body)
    _assert_single_terminal(body, events)
    assert [name for name, _ in events if name == "token"], "必须至少有一个 token 事件"

    done = events[-1][1]
    assert response.headers[REQUEST_ID_HEADER] == done["request_id"]

    citations = [payload for name, payload in events if name == "citation"]
    assert done["citation_count"] == len(citations)
    for payload in citations:
        assert set(payload) == set(CITATION_FIELDS)
    indices = [payload["citation_index"] for payload in citations]
    assert indices == list(range(1, len(citations) + 1))


def test_sse_never_emits_events_outside_the_contract(ingest_demo, client) -> None:
    ingest_demo()
    response = client.post(CHAT_URL, json=_body({"role": "user", "content": "数据结构属于什么课程类别？"}))
    events = parse_sse(response.content)
    assert {name for name, _ in events} <= set(SSE_EVENTS)
    _assert_single_terminal(response.content, events)


def test_sse_data_payload_never_contains_internal_diagnostics(ingest_demo, client) -> None:
    ingest_demo()
    response = client.post(CHAT_URL, json=_body({"role": "user", "content": "毕业总学分是多少？"}))
    for name, payload in parse_sse(response.content):
        if name != "citation":
            continue
        for forbidden in ("score", "fused", "rank", "path", "storage", "chunk_index", "file_type"):
            assert forbidden not in json.dumps(payload), f"引用事件泄漏内部字段：{forbidden}"
