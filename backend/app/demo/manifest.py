"""只读演示 manifest 校验。

后端 Docker 构建上下文只有 ``backend/``，因此这里独立实现校验，
不得 import ``scripts/demo_corpus``。校验只读取 ``/app/demo``，永不写入。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.constants import SUPPORTED_EXTENSIONS
from app.core.errors import (
    DEMO_FILE_CHECKSUM_MISMATCH,
    DEMO_MANIFEST_INVALID,
    DEMO_MANIFEST_NOT_FOUND,
    ApiError,
)

MANIFEST_FILE_NAME = "manifest.json"
CORPUS_DIR_NAME = "corpus"
PATH_PREFIX = f"{CORPUS_DIR_NAME}/"


@dataclass(frozen=True)
class ManifestDocument:
    path: str
    sha256: str
    file_type: str
    doc_category: str
    title: str
    file_version: str
    effective_from: str
    size_bytes: int

    @property
    def file_name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class DemoManifest:
    dataset_version: str
    school_name: str
    manifest_sha256: str
    dataset_sha256: str
    manifest_path: Path
    dataset_root: Path
    documents: tuple[ManifestDocument, ...]

    def source_key(self, document: ManifestDocument) -> str:
        return f"{self.dataset_version}:{document.path}"


def _invalid(reason: str) -> ApiError:
    return ApiError(DEMO_MANIFEST_INVALID, details={"reason": reason})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256(path: Path) -> str:
    return sha256_file(path)


def dataset_digest(documents: list[dict]) -> str:
    """与生成器一致：按 path 排序后拼接 ``path\\nsha256\\n`` 求 SHA-256。"""
    payload = "".join(f"{doc['path']}\n{doc.get('sha256', '')}\n" for doc in documents)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_path_safety(relative: str, dataset_root: Path) -> Path:
    if not relative.startswith(PATH_PREFIX):
        raise _invalid("path_outside_corpus")
    if "\\" in relative or ".." in relative or relative.startswith("/"):
        raise _invalid("unsafe_path")
    candidate = dataset_root / relative
    try:
        resolved_root = dataset_root.resolve()
        resolved = candidate.resolve()
    except OSError as error:
        raise _invalid("path_unresolvable") from error
    if resolved_root not in resolved.parents:
        raise _invalid("path_escape")
    return resolved


def load_manifest(dataset_path: str | Path, expected_version: str) -> DemoManifest:
    """加载并完整校验 manifest；任何不一致都抛出安全错误。"""
    dataset_root = Path(dataset_path)
    manifest_path = dataset_root / MANIFEST_FILE_NAME
    if not manifest_path.is_file():
        raise ApiError(DEMO_MANIFEST_NOT_FOUND)

    raw = manifest_path.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid("manifest_not_json") from error
    if not isinstance(manifest, dict):
        raise _invalid("manifest_not_object")

    if manifest.get("fictional") is not True:
        raise _invalid("fictional_not_true")
    if manifest.get("dataset_version") != expected_version:
        raise _invalid("dataset_version_mismatch")

    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise _invalid("documents_missing")

    paths = [doc.get("path") for doc in documents if isinstance(doc, dict)]
    if len(paths) != len(documents):
        raise _invalid("document_entry_invalid")
    if paths != sorted(paths):
        raise _invalid("documents_not_sorted")
    if len(set(paths)) != len(paths):
        raise _invalid("duplicate_paths")

    expected_digest = manifest.get("dataset_sha256")
    if dataset_digest(documents) != expected_digest:
        raise _invalid("dataset_sha256_mismatch")

    parsed: list[ManifestDocument] = []
    for entry in documents:
        relative = entry["path"]
        resolved = _check_path_safety(relative, dataset_root)
        if not resolved.is_file():
            raise _invalid("manifest_file_missing")

        extension = resolved.suffix.lower()
        file_type = SUPPORTED_EXTENSIONS.get(extension)
        if file_type is None or entry.get("file_type") != file_type:
            raise _invalid("file_type_extension_mismatch")

        if sha256_file(resolved) != entry.get("sha256"):
            raise ApiError(DEMO_FILE_CHECKSUM_MISMATCH)

        parsed.append(
            ManifestDocument(
                path=relative,
                sha256=str(entry.get("sha256", "")),
                file_type=file_type,
                doc_category=str(entry.get("doc_category", "unknown")),
                title=str(entry.get("title", "")),
                file_version=str(entry.get("version", "")),
                effective_from=str(entry.get("effective_from", "")),
                size_bytes=resolved.stat().st_size,
            )
        )

    corpus_dir = dataset_root / CORPUS_DIR_NAME
    on_disk = {
        f"{PATH_PREFIX}{item.name}"
        for item in corpus_dir.iterdir()
        if item.is_file()
    } if corpus_dir.is_dir() else set()
    if on_disk != set(paths):
        raise _invalid("corpus_file_set_mismatch")

    return DemoManifest(
        dataset_version=str(manifest.get("dataset_version")),
        school_name=str(manifest.get("school_name", "")),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        dataset_sha256=str(expected_digest),
        manifest_path=manifest_path,
        dataset_root=dataset_root,
        documents=tuple(parsed),
    )
