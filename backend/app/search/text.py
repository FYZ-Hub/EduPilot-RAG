"""确定性文本规范化与 CJK 检索辅助（叶子模块）。

FTS5 的 ``unicode61`` 分词器把整段 CJK 当作**一个 token**
（例如「学分认定」不会被切成「学分」「认定」），因此中文条款用短词无法召回。
这里用**确定性 CJK 二元组（bigram）**作为辅助列的取值，并且查询侧使用同一套
规范化逻辑，保证「索引与查询同规则、版本化、可复现」。
原始 chunk 正文不会被修改。
"""

from __future__ import annotations

import re
import unicodedata

from app.search.schema import FTS_NGRAM_VERSION, FTS_NORMALIZATION_VERSION

# FTS5 查询语法字符、下划线与空白：查询侧一律不作为 token 内容
_QUERY_SEPARATORS = re.compile(r"[\W_]+", re.UNICODE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")

MAX_QUERY_CHARS = 400
MAX_TERM_CHARS = 64
# 单个 CJK 词超过该长度时，AND 语义过严，退化为 OR（自然语言问句场景）
CJK_TERM_AND_LIMIT = 6


def normalize_text(text: str) -> str:
    """NFC + 控制字符清理 + 空白折叠 + 小写（仅用于索引/查询，不改原始正文）。"""
    if not text:
        return ""
    cleaned = _CONTROL_RE.sub("", unicodedata.normalize("NFC", text))
    return _WHITESPACE_RE.sub(" ", cleaned).strip().lower()


def is_cjk(character: str) -> bool:
    return (
        "\u3400" <= character <= "\u4dbf" or "\u4e00" <= character <= "\u9fff"
    )


def cjk_runs(text: str) -> list[str]:
    """返回文本中所有连续 CJK 片段，顺序稳定。"""
    runs: list[str] = []
    buffer: list[str] = []
    for character in text:
        if is_cjk(character):
            buffer.append(character)
            continue
        if buffer:
            runs.append("".join(buffer))
            buffer = []
    if buffer:
        runs.append("".join(buffer))
    return runs


def cjk_bigrams(text: str) -> str:
    """确定性 CJK 二元组文本；单字片段保留自身。空白分隔，便于分词。"""
    grams: list[str] = []
    for run in cjk_runs(normalize_text(text)):
        if len(run) == 1:
            grams.append(run)
            continue
        grams.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(grams)


def split_query_terms(query: str) -> list[str]:
    """按空白/标点切分查询词；不含任何 FTS 语法字符。"""
    normalized = normalize_text(query)
    return [term for term in _QUERY_SEPARATORS.split(normalized) if term]


def term_sub_tokens(term: str) -> list[str]:
    """把一个查询词展开为子 token：CJK 片段转二元组，其余保留原词。"""
    tokens: list[str] = []
    for run in cjk_runs(term):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    non_cjk = "".join(" " if is_cjk(char) else char for char in term)
    tokens.extend(part for part in re.split(r"[\W_]+", non_cjk, flags=re.UNICODE) if part)
    # 去重并保持首次出现顺序
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def quote_token(token: str) -> str:
    """FTS5 字符串字面量：用双引号包裹，内部双引号翻倍。"""
    return '"' + token.replace('"', '""') + '"'


def build_match_expressions(query: str) -> tuple[str, str]:
    """返回 ``(and_expression, or_expression)``；空查询返回 ``("", "")``。

    所有 token **一律**用引号包裹（包括只含一个 token 的查询词），
    因此用户输入中的引号、括号、减号、星号、冒号以及 ``AND``/``OR``/``NOT``/``NEAR``
    等 FTS 关键字都只被当作普通文本，不可能注入或改写查询语义。
    """
    terms = split_query_terms(query)
    if not terms:
        return "", ""

    expressions: list[str] = []
    for term in terms[:MAX_TERM_CHARS]:
        tokens = term_sub_tokens(term[:MAX_TERM_CHARS])
        if not tokens:
            continue
        quoted = [quote_token(token) for token in tokens]
        if len(quoted) == 1:
            expressions.append(quoted[0])
            continue
        # 短中文词用 AND（精确），长中文短语用 OR（召回），两侧均为固定规则
        is_single_cjk_run = len(cjk_runs(term)) == 1 and all(is_cjk(char) for char in term)
        joiner = " AND " if (not is_single_cjk_run or len(term) <= CJK_TERM_AND_LIMIT) else " OR "
        expressions.append(joiner.join(quoted))

    if not expressions:
        return "", ""
    return " AND ".join(expressions), " OR ".join(expressions)


__all__ = [
    "CJK_TERM_AND_LIMIT",
    "FTS_NGRAM_VERSION",
    "FTS_NORMALIZATION_VERSION",
    "MAX_QUERY_CHARS",
    "build_match_expressions",
    "cjk_bigrams",
    "cjk_runs",
    "is_cjk",
    "normalize_text",
    "quote_token",
    "split_query_terms",
    "term_sub_tokens",
]
