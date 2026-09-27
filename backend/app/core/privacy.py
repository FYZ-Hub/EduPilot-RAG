"""外部模型调用的个人信息清洗（确定性、幂等、离线）。

用途：在**把文本发送给外部 Provider 之前**，对**外发副本**做一次确定性清洗，
降低把学生个人信息交给第三方模型的风险（PRODUCT_SPEC 9）。

约束：

- 完全确定性：同样的输入永远得到同样的输出，不使用随机数、时间或哈希。
- 幂等：对已清洗文本再清洗一次结果不变。
- 离线：不访问网络、不调用任何模型、不读取任何外部资源。
- 只清洗外发副本：绝不修改 ``RetrievedChunk`` / ``citation`` / ``quote``、
  SQLite、Chroma、FTS 或任何原始数据。
- 日志不得记录清洗前的内容（调用方只应记录本文档定义的稳定占位符与计数）。

策略版本化：``PRIVACY_POLICY_VERSION`` 变化会改变 API Embedding 与 Reranker 的
descriptor/fingerprint，从而按既有机制触发 API 向量索引重建。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# 清洗策略版本；变化即改变外部 API Provider 的指纹语义
PRIVACY_POLICY_VERSION = "external-privacy-v1"

REDACTED_NAME = "[REDACTED_NAME]"
REDACTED_STUDENT_ID = "[REDACTED_STUDENT_ID]"
REDACTED_EMAIL = "[REDACTED_EMAIL]"
REDACTED_PHONE = "[REDACTED_PHONE]"
REDACTED_ID = "[REDACTED_ID]"

# 标签值：不含空白、逗号、句号、分号与冒号，长度有界
_LABEL_VALUE = r"[^\s,，。;；:：]{1,64}"

# 顺序敏感：先处理带标签的强标识，再处理通用格式。
# 每条规则都必须保持幂等（占位符本身不会再被同一规则改写）。
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"(?P<label>学生姓名|真实姓名|姓\s*名)\s*[:：]\s*{_LABEL_VALUE}"
        ),
        rf"\g<label>：{REDACTED_NAME}",
    ),
    (
        re.compile(
            rf"(?P<label>学生编号|学生证号|人员编号|个人编号|学籍号|学\s*号)"
            rf"\s*[:：]\s*{_LABEL_VALUE}"
        ),
        rf"\g<label>：{REDACTED_STUDENT_ID}",
    ),
    (
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        REDACTED_EMAIL,
    ),
    (
        # 15 位或 18 位（最后一位可为 X）身份证号，两侧不得紧邻数字
        re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)"),
        REDACTED_ID,
    ),
    (
        # 中国大陆手机号，两侧不得紧邻数字
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        REDACTED_PHONE,
    ),
)


def scrub(text: str) -> str:
    """返回清洗后的文本副本；空值安全，幂等，确定性。"""
    value = text or ""
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def scrub_texts(texts: Sequence[str]) -> list[str]:
    """按输入顺序返回清洗后的**新列表**；调用方持有的原文本不被修改。"""
    return [scrub(text) for text in texts]


__all__ = [
    "PRIVACY_POLICY_VERSION",
    "REDACTED_EMAIL",
    "REDACTED_ID",
    "REDACTED_NAME",
    "REDACTED_PHONE",
    "REDACTED_STUDENT_ID",
    "scrub",
    "scrub_texts",
]
