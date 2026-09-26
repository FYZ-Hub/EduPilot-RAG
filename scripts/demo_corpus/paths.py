"""仓库路径安全策略。

生成器只允许写入仓库 ``.tmp/`` 下的子目录，且只允许固化到仓库 ``demo/``；
任何用户提供的路径都不会被递归删除。
"""

from __future__ import annotations

from pathlib import Path

# 仓库根目录：scripts/demo_corpus/paths.py -> scripts/demo_corpus -> scripts -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]

TMP_DIRNAME = ".tmp"
PUBLISH_DIRNAME = "demo"

# 这些目录（自身、子目录与祖先目录）永远不能作为生成输出或发布目标
RESERVED_RELATIVE = (
    "demo",
    "demo/corpus",
    "scripts",
    "backend",
    "frontend",
    "docs",
    ".trae",
    ".git",
)


class UnsafePathError(ValueError):
    """用户提供的路径不安全。"""


class UnsafeOutputPathError(UnsafePathError):
    """``--output`` 路径不安全。"""


class UnsafePublishPathError(UnsafePathError):
    """``--publish`` 路径不安全。"""


class OutputDirectoryNotEmptyError(RuntimeError):
    """输出目录已存在且非空，拒绝覆盖且不删除任何文件。"""


def repo_root() -> Path:
    return REPO_ROOT


def _absolute(value) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _reserved_paths(root: Path) -> dict[str, Path]:
    return {name: (root / name).resolve() for name in RESERVED_RELATIVE}


def _reject_root_cwd_and_reserved(candidate: Path, root: Path, error=UnsafePathError) -> None:
    """拒绝文件系统根、当前工作目录、仓库根及其祖先，以及受保护目录自身/子目录/祖先。"""
    if candidate.parent == candidate:
        raise error(f"拒绝使用文件系统根目录：{candidate}")
    if candidate == Path.cwd().resolve():
        raise error(f"拒绝使用当前工作目录：{candidate}")
    if candidate == root or candidate in root.parents:
        raise error(f"拒绝使用仓库根目录或其祖先目录：{candidate}")
    for name, path in _reserved_paths(root).items():
        if candidate == path:
            raise error(f"拒绝使用受保护目录 {name}：{candidate}")
        if path in candidate.parents:
            raise error(f"拒绝使用受保护目录 {name} 的子目录：{candidate}")
        if candidate in path.parents:
            raise error(f"拒绝使用受保护目录 {name} 的祖先目录：{candidate}")


def resolve_output_dir(value, *, repo_root_path: Path | None = None) -> Path:
    """``--output`` 只允许仓库 ``.tmp/`` 之下、且不等于 ``.tmp`` 本身的目录。"""
    root = Path(repo_root_path or REPO_ROOT).resolve()
    candidate = _absolute(value)
    tmp_root = (root / TMP_DIRNAME).resolve()

    if candidate == tmp_root:
        raise UnsafeOutputPathError(f"--output 不能是仓库 {TMP_DIRNAME}/ 目录本身：{candidate}")
    if tmp_root not in candidate.parents:
        raise UnsafeOutputPathError(
            f"--output 必须是仓库 {TMP_DIRNAME}/ 下的子目录（例如 {TMP_DIRNAME}/demo-generated）：{candidate}"
        )
    _reject_root_cwd_and_reserved(candidate, root, UnsafeOutputPathError)
    return candidate


def resolve_publish_dir(value, *, repo_root_path: Path | None = None) -> Path:
    """``--publish`` 只允许仓库 ``demo/`` 目录。"""
    root = Path(repo_root_path or REPO_ROOT).resolve()
    candidate = _absolute(value)
    demo_root = (root / PUBLISH_DIRNAME).resolve()

    if candidate != demo_root:
        raise UnsafePublishPathError(f"--publish 只允许仓库 {PUBLISH_DIRNAME}/ 目录：{candidate}")
    if candidate.parent == candidate or candidate == Path.cwd().resolve():
        raise UnsafePublishPathError(f"--publish 路径不安全：{candidate}")
    return candidate


def assert_disjoint(output_dir: Path, publish_dir: Path | None) -> None:
    """输出目录与发布目录不得相同或互相嵌套。"""
    if publish_dir is None:
        return
    output_dir = Path(output_dir).resolve()
    publish_dir = Path(publish_dir).resolve()
    if output_dir == publish_dir:
        raise UnsafeOutputPathError(f"--output 与 --publish 不得是同一目录：{output_dir}")
    if publish_dir in output_dir.parents or output_dir in publish_dir.parents:
        raise UnsafeOutputPathError(
            f"--output 与 --publish 不得互相嵌套：{output_dir} / {publish_dir}"
        )


def prepare_output_dir(output_dir: Path) -> Path:
    """创建输出目录；已存在且非空时安全失败，绝不删除其中任何文件。"""
    output_dir = Path(output_dir)
    if output_dir.exists():
        if not output_dir.is_dir():
            raise OutputDirectoryNotEmptyError(f"输出路径已存在且不是目录：{output_dir}")
        if any(output_dir.iterdir()):
            raise OutputDirectoryNotEmptyError(
                f"输出目录已存在且非空，拒绝覆盖且不会删除任何文件：{output_dir}"
            )
    else:
        output_dir.mkdir(parents=True)
    return output_dir
