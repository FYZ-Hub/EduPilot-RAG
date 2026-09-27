"""外部模型调用的个人信息清洗（确定性、幂等、离线）。

用途：在**把文本发送给外部 Provider 之前**，对**外发副本**做一次确定性清洗，
降低把学生个人信息交给第三方模型的风险（PRODUCT_SPEC 9）。

约束：

- 完全确定性：同样的输入永远得到同样的输出，不使用随机数、时间或哈希。
- 幂等：对已清洗文本再清洗一次结果不变。
- 离线：不访问网络、不调用任何模型（无 NER）、不引入任何新依赖。
- 只清洗外发副本：绝不修改 ``RetrievedChunk`` / ``citation`` / ``quote``、
  SQLite、Chroma、FTS 或任何原始数据。
- 不误清洗学术术语：课程代码（如 ``QM-CS201``）、课程名称、学分、学期、日期、普通数字
  必须原样保留。
- 日志不得记录清洗前的内容（调用方只应记录本文档定义的稳定占位符与计数）。

策略版本（v1 / v2 / v3 均为**历史版本**，当前有效版本为 **v4**）：

- ``external-privacy-v1``（历史）：仅覆盖「标签 + 冒号 + 无空格短值」。
- ``external-privacy-v2``（历史）：扩展键值格式（冒号 / 等号 / 空白 / TAB / 表格竖线），
  允许标签值含内部空格，补充中国大陆固定电话。
- ``external-privacy-v3``（历史）：新增学术字段边界（课程编号 / 课程名称 / 学分 / 学期 /
  成绩 …）修正「误删同一行学术字段」，并给字段值加上真实长度上限。
- ``external-privacy-v4``（**当前**）：Unicode 收尾。标签、分隔符、字段边界与电话规则
  统一支持常见**横向 Unicode 空白**（NBSP、全角空格、各档排版空格，且**不跨行**），
  固定电话兼容 ASCII / 全角括号与 ASCII 连字符 / 全角连字符 / en dash / em dash，
  并支持区号与号码之间的一个或多个横向空白。

**值语义变化 ⇒ 版本必须提升**；当前版本进入 API Embedding 与 Reranker 的
descriptor/fingerprint。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# 清洗策略版本；变化即改变外部 API Provider 的指纹语义
PRIVACY_POLICY_VERSION = "external-privacy-v4"

REDACTED_NAME = "[REDACTED_NAME]"
REDACTED_STUDENT_ID = "[REDACTED_STUDENT_ID]"
REDACTED_EMAIL = "[REDACTED_EMAIL]"
REDACTED_PHONE = "[REDACTED_PHONE]"
REDACTED_ID = "[REDACTED_ID]"

# --- Unicode 横向空白与标点 ---------------------------------------------------
# 常见横向空白：空格 / TAB / NBSP / 全角空格 / 各档排版空格。
# 刻意**不含** CR、LF、VT、FF 与 U+2028 / U+2029：匹配绝不跨行。
_HSPACE_CHARS = r" \t\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000"
_HSPACE = rf"[{_HSPACE_CHARS}]"
# 固定电话连接符：ASCII 连字符 / 全角连字符 / en dash / em dash
_HYPHEN_CHARS = r"\-－–—"
# 区号与号码之间的分隔（空白或连接符的任意组合，可为空）
_PHONE_GAP = rf"[{_HSPACE_CHARS}{_HYPHEN_CHARS}]*"
_OPEN_PAREN = r"[（(]"
_CLOSE_PAREN = r"[）)]"

# --- 字段值长度上限（真实上限：单 token ×(1+额外 token 数)） ------------------
_VALUE_TOKEN_MAX_CHARS = 48
_VALUE_EXTRA_TOKENS = 4
#: 单个 PII 字段值允许的最大字符数（超出即截断，绝不无界吞掉后续内容）
MAX_FIELD_VALUE_CHARS = _VALUE_TOKEN_MAX_CHARS * (1 + _VALUE_EXTRA_TOKENS)

# --- 标签（只用横向空白连接，不跨行） ----------------------------------------
_NAME_LABELS = rf"学生姓名|真实姓名|姓{_HSPACE}*名"
_STUDENT_ID_LABELS = rf"学生编号|学生证号|人员编号|个人编号|学籍号|学{_HSPACE}*号"
_PHONE_LABELS = r"联系电话|联系方式|固定电话|手机号码|手机号|电话|手机"

# PII 标签：出现即说明「这里开始是个人信息」
_STOP_LABELS = (
    rf"(?:{_NAME_LABELS}|{_STUDENT_ID_LABELS}|{_PHONE_LABELS}"
    r"|电子邮箱|电子信箱|邮箱|E-?mail|身份证号|身份证|证件号)"
)

# 普通学术/业务字段名：出现即说明「上一个 PII 字段已经结束」。
# 这些字段只用于**截断** PII 值，自身永远不会被清洗。
_FIELD_BOUNDARY_LABELS = (
    r"课程编号|课程代码|课程名称|课程类别|课程性质"
    r"|学分|学期|成绩|绩点"
    r"|专业|年级|班级|学院|培养层次"
    r"|日期|时间|地点|教室|校区"
    r"|状态|类型|备注|说明"
)

# 参与「截断」的全部标签
_BOUNDARY_LABELS = rf"(?:{_STOP_LABELS}|{_FIELD_BOUNDARY_LABELS})"

# 键值分隔符：冒号 / 等号 / 表格竖线 / 横向空白（含全角空格与 NBSP）
_SEPARATOR = rf"(?:{_HSPACE}*[:：=＝]{_HSPACE}*|{_HSPACE}*[|｜]{_HSPACE}*|{_HSPACE}+)"

# 任意「字段名 + 冒号/等号」也是边界（例如 培养层次：本科）
_GENERIC_KEY_BOUNDARY = rf"[^\s|｜,，。;；:：=＝]{{1,24}}{_HSPACE}*[:：=＝]"

# 值：允许内部横向空白，但不以「下一个字段」为起点，也不吞掉竖线/换行/句读；
# 单 token 与 token 数量都有硬上限 ⇒ 总长度真实有界。
_STOP_AHEAD = (
    rf"(?!(?:{_BOUNDARY_LABELS})(?:{_HSPACE}*[:：=＝]|{_HSPACE}*[|｜]|{_HSPACE})"
    rf"|{_GENERIC_KEY_BOUNDARY})"
)
_VALUE_TOKEN = rf"(?:{_STOP_AHEAD}[^\s|｜,，。;；:：=＝])"
_VALUE = (
    rf"{_VALUE_TOKEN}{{1,{_VALUE_TOKEN_MAX_CHARS}}}"
    rf"(?:{_HSPACE}+{_VALUE_TOKEN}{{1,{_VALUE_TOKEN_MAX_CHARS}}}){{0,{_VALUE_EXTRA_TOKENS}}}"
)

# 中国大陆手机号 / 固定电话：兼容 ASCII 与全角括号、ASCII 连字符 / 全角连字符 /
# en dash / em dash，以及区号与号码之间的一个或多个横向空白。
_PHONE_VALUE = (
    rf"(?:\+?86{_PHONE_GAP})?"
    rf"(?:1[3-9]\d{{9}}|(?:{_OPEN_PAREN}{_HSPACE}*0\d{{2,3}}{_HSPACE}*{_CLOSE_PAREN}"
    rf"|0\d{{2,3}}){_PHONE_GAP}\d{{7,8}})"
)

# 顺序敏感：先处理带标签的强标识，再处理通用格式。
# 每条规则都必须保持幂等（占位符本身不会再被同一规则改写）。
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(rf"(?P<label>{_NAME_LABELS}){_SEPARATOR}(?P<value>{_VALUE})"),
        rf"\g<label>：{REDACTED_NAME}",
    ),
    (
        re.compile(rf"(?P<label>{_STUDENT_ID_LABELS}){_SEPARATOR}(?P<value>{_VALUE})"),
        rf"\g<label>：{REDACTED_STUDENT_ID}",
    ),
    (
        re.compile(rf"(?P<label>{_PHONE_LABELS}){_SEPARATOR}(?P<value>{_PHONE_VALUE})"),
        rf"\g<label>：{REDACTED_PHONE}",
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
    (
        # 常见固定电话：区号 0xx / 0xxx，兼容全角括号与多种连字符 / 横向空白
        re.compile(
            rf"(?<![\d{_HYPHEN_CHARS}])"
            rf"(?:{_OPEN_PAREN}{_HSPACE}*0\d{{2,3}}{_HSPACE}*{_CLOSE_PAREN}|0\d{{2,3}})"
            rf"{_PHONE_GAP}\d{{7,8}}(?!\d)"
        ),
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
    "MAX_FIELD_VALUE_CHARS",
    "PRIVACY_POLICY_VERSION",
    "REDACTED_EMAIL",
    "REDACTED_ID",
    "REDACTED_NAME",
    "REDACTED_PHONE",
    "REDACTED_STUDENT_ID",
    "scrub",
    "scrub_texts",
]
