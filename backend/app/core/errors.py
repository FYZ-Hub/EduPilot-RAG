"""统一错误码与安全错误体。

所有 4xx/5xx 响应都使用 ``{code, message, details, request_id}``；
不得返回堆栈、SQL、绝对路径、文档正文、密钥或内部异常文本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- 文档与上传 ---
DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
DOCUMENT_INVALID_TYPE = "DOCUMENT_INVALID_TYPE"
DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
DOCUMENT_EMPTY = "DOCUMENT_EMPTY"
DOCUMENT_CORRUPT = "DOCUMENT_CORRUPT"
DOCUMENT_CONTENT_TYPE_MISMATCH = "DOCUMENT_CONTENT_TYPE_MISMATCH"
DOCUMENT_UNSAFE_CONTAINER = "DOCUMENT_UNSAFE_CONTAINER"
DOCUMENT_UNSAFE_NAME = "DOCUMENT_UNSAFE_NAME"
DOCUMENT_MISSING_FILE = "DOCUMENT_MISSING_FILE"
DOCUMENT_RETRY_NOT_ALLOWED = "DOCUMENT_RETRY_NOT_ALLOWED"
DOCUMENT_PARSE_FAILED = "DOCUMENT_PARSE_FAILED"
DOCUMENT_IO_ERROR = "DOCUMENT_IO_ERROR"
DOCUMENT_CHUNK_FAILED = "DOCUMENT_CHUNK_FAILED"
DOCUMENT_EMBEDDING_FAILED = "DOCUMENT_EMBEDDING_FAILED"
DOCUMENT_INDEX_FAILED = "DOCUMENT_INDEX_FAILED"
DOCUMENT_KEYWORD_INDEX_FAILED = "DOCUMENT_KEYWORD_INDEX_FAILED"

# --- 检索 ---
SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
RETRIEVAL_QUERY_INVALID = "RETRIEVAL_QUERY_INVALID"
RETRIEVAL_FILTER_INVALID = "RETRIEVAL_FILTER_INVALID"
RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"

# --- Reranker（阶段 5；查询时能力，不产生持久索引）---
RERANK_PROVIDER_UNAVAILABLE = "RERANK_PROVIDER_UNAVAILABLE"
RERANK_PROVIDER_FORBIDDEN = "RERANK_PROVIDER_FORBIDDEN"
RERANK_RESPONSE_INVALID = "RERANK_RESPONSE_INVALID"

# --- Embedding / 向量索引 ---
EMBEDDING_PROVIDER_UNAVAILABLE = "EMBEDDING_PROVIDER_UNAVAILABLE"
EMBEDDING_PROVIDER_FORBIDDEN = "EMBEDDING_PROVIDER_FORBIDDEN"
EMBEDDING_DIMENSION_MISMATCH = "EMBEDDING_DIMENSION_MISMATCH"
VECTOR_STORE_UNAVAILABLE = "VECTOR_STORE_UNAVAILABLE"
VECTOR_COLLECTION_MISMATCH = "VECTOR_COLLECTION_MISMATCH"

# --- 演示数据集 ---
DEMO_DATASET_DISABLED = "DEMO_DATASET_DISABLED"
DEMO_MANIFEST_NOT_FOUND = "DEMO_MANIFEST_NOT_FOUND"
DEMO_MANIFEST_INVALID = "DEMO_MANIFEST_INVALID"
DEMO_FILE_CHECKSUM_MISMATCH = "DEMO_FILE_CHECKSUM_MISMATCH"
DEMO_JOB_NOT_FOUND = "DEMO_JOB_NOT_FOUND"
DEMO_PIPELINE_UNAVAILABLE = "DEMO_PIPELINE_UNAVAILABLE"
DEMO_DATASET_CHANGED = "DEMO_DATASET_CHANGED"
DEMO_PIPELINE_CHANGED = "DEMO_PIPELINE_CHANGED"
DEMO_ACTIVATION_FAILED = "DEMO_ACTIVATION_FAILED"

# --- 通用 ---
REQUEST_VALIDATION_ERROR = "REQUEST_VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
HTTP_ERROR = "HTTP_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class ErrorSpec:
    status_code: int
    message: str
    retryable: bool = False


ERROR_SPECS: dict[str, ErrorSpec] = {
    DOCUMENT_NOT_FOUND: ErrorSpec(404, "文档不存在或已被删除"),
    DOCUMENT_INVALID_TYPE: ErrorSpec(400, "仅支持 PDF、DOCX、XLSX 文件"),
    DOCUMENT_TOO_LARGE: ErrorSpec(413, "文件超过允许的最大体积"),
    DOCUMENT_EMPTY: ErrorSpec(400, "文件内容为空"),
    DOCUMENT_CORRUPT: ErrorSpec(400, "文件已损坏或无法解析", retryable=True),
    DOCUMENT_CONTENT_TYPE_MISMATCH: ErrorSpec(400, "文件类型与扩展名或声明不一致"),
    DOCUMENT_UNSAFE_CONTAINER: ErrorSpec(400, "文件包含不允许的宏、嵌入对象或外部引用"),
    DOCUMENT_UNSAFE_NAME: ErrorSpec(400, "文件名不合法"),
    DOCUMENT_MISSING_FILE: ErrorSpec(400, "请求缺少文件字段"),
    DOCUMENT_RETRY_NOT_ALLOWED: ErrorSpec(409, "该文档失败且不可重试"),
    DOCUMENT_PARSE_FAILED: ErrorSpec(500, "文档解析失败", retryable=True),
    DOCUMENT_IO_ERROR: ErrorSpec(500, "文档读写失败", retryable=True),
    DOCUMENT_CHUNK_FAILED: ErrorSpec(500, "文档切片失败", retryable=True),
    DOCUMENT_EMBEDDING_FAILED: ErrorSpec(500, "向量生成失败", retryable=True),
    DOCUMENT_INDEX_FAILED: ErrorSpec(500, "向量索引写入或对账失败", retryable=True),
    DOCUMENT_KEYWORD_INDEX_FAILED: ErrorSpec(500, "关键词索引写入或对账失败", retryable=True),
    SOURCE_NOT_FOUND: ErrorSpec(404, "来源不存在或当前不可检索"),
    RETRIEVAL_QUERY_INVALID: ErrorSpec(400, "检索查询不合法"),
    RETRIEVAL_FILTER_INVALID: ErrorSpec(400, "检索过滤条件不合法"),
    RETRIEVAL_UNAVAILABLE: ErrorSpec(503, "检索能力当前不可用", retryable=True),
    RERANK_PROVIDER_UNAVAILABLE: ErrorSpec(503, "Reranker Provider 当前不可用", retryable=True),
    RERANK_PROVIDER_FORBIDDEN: ErrorSpec(500, "生产环境禁止使用 Fake Reranker Provider"),
    RERANK_RESPONSE_INVALID: ErrorSpec(500, "Reranker 返回结构不合法"),
    EMBEDDING_PROVIDER_UNAVAILABLE: ErrorSpec(503, "Embedding Provider 当前不可用"),
    EMBEDDING_PROVIDER_FORBIDDEN: ErrorSpec(500, "生产环境禁止使用 Fake Embedding Provider"),
    EMBEDDING_DIMENSION_MISMATCH: ErrorSpec(500, "向量维度与配置或已有集合不一致"),
    VECTOR_STORE_UNAVAILABLE: ErrorSpec(503, "向量库当前不可用"),
    VECTOR_COLLECTION_MISMATCH: ErrorSpec(500, "向量集合与当前流水线配置不一致"),
    DEMO_DATASET_DISABLED: ErrorSpec(409, "演示数据集功能已禁用"),
    DEMO_MANIFEST_NOT_FOUND: ErrorSpec(422, "演示资料 manifest 不存在"),
    DEMO_MANIFEST_INVALID: ErrorSpec(422, "演示资料 manifest 校验失败"),
    DEMO_FILE_CHECKSUM_MISMATCH: ErrorSpec(422, "演示资料校验值不匹配"),
    DEMO_JOB_NOT_FOUND: ErrorSpec(404, "演示任务不存在"),
    DEMO_PIPELINE_UNAVAILABLE: ErrorSpec(503, "演示流水线当前不可用", retryable=True),
    DEMO_DATASET_CHANGED: ErrorSpec(409, "演示数据集在任务期间发生变化"),
    DEMO_PIPELINE_CHANGED: ErrorSpec(409, "流水线版本在任务期间发生变化"),
    DEMO_ACTIVATION_FAILED: ErrorSpec(500, "演示数据集激活前置条件未满足"),
    REQUEST_VALIDATION_ERROR: ErrorSpec(422, "请求参数不合法"),
    NOT_FOUND: ErrorSpec(404, "请求的资源不存在"),
    METHOD_NOT_ALLOWED: ErrorSpec(405, "请求方法不被支持"),
    HTTP_ERROR: ErrorSpec(400, "请求无法处理"),
    INTERNAL_ERROR: ErrorSpec(500, "服务内部错误", retryable=True),
}


@dataclass
class ApiError(Exception):
    """携带安全展示文案的领域错误；``details`` 不得包含宿主机路径或正文。"""

    code: str
    message: str | None = None
    status_code: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool | None = None

    def __post_init__(self) -> None:
        spec = ERROR_SPECS.get(self.code)
        if self.status_code is None:
            self.status_code = spec.status_code if spec else 500
        if self.message is None:
            self.message = spec.message if spec else "请求处理失败"
        if self.retryable is None:
            self.retryable = spec.retryable if spec else False
        super().__init__(self.code)

    def error_object(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": bool(self.retryable)}

    def to_payload(self, request_id: str) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
            "request_id": request_id,
        }
