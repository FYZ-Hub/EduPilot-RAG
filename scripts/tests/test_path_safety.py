"""输出目录安全策略回归测试。

全部使用隔离的临时目录与 sentinel 文件；
拒绝逻辑只检查路径，不会真的尝试删除仓库目录。
"""

from __future__ import annotations

import pytest

from conftest import FONT_PATH, generate
from demo_corpus.paths import (
    OutputDirectoryNotEmptyError,
    UnsafeOutputPathError,
    UnsafePublishPathError,
    assert_disjoint,
    resolve_output_dir,
    resolve_publish_dir,
)

SENTINEL_CONTENT = "sentinel-keep-me"


@pytest.fixture
def fake_repo(tmp_path):
    """模拟仓库目录结构，并在 demo/corpus 放入 sentinel 文件。"""
    repo = tmp_path / "repo"
    for name in (".tmp", "demo", "demo/corpus", "scripts", "backend", "frontend", "docs", ".trae", ".git"):
        (repo / name).mkdir(parents=True, exist_ok=True)
    sentinel = repo / "demo" / "corpus" / "sentinel.txt"
    sentinel.write_text(SENTINEL_CONTENT, encoding="utf-8")
    return repo, sentinel


def _assert_sentinel(sentinel):
    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == SENTINEL_CONTENT


def test_output_allows_only_tmp_subdirectory(fake_repo):
    repo, sentinel = fake_repo
    resolved = resolve_output_dir(repo / ".tmp" / "demo-generated", repo_root_path=repo)
    assert resolved == (repo / ".tmp" / "demo-generated").resolve()
    _assert_sentinel(sentinel)


def test_output_rejects_tmp_directory_itself(fake_repo):
    repo, sentinel = fake_repo
    with pytest.raises(UnsafeOutputPathError):
        resolve_output_dir(repo / ".tmp", repo_root_path=repo)
    _assert_sentinel(sentinel)


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "demo",
        "demo/corpus",
        "scripts",
        "backend",
        "frontend",
        "docs",
        ".trae",
        ".git",
    ],
)
def test_output_rejects_repository_directories(fake_repo, relative):
    repo, sentinel = fake_repo
    with pytest.raises(UnsafeOutputPathError):
        resolve_output_dir(repo / relative if relative else repo, repo_root_path=repo)
    _assert_sentinel(sentinel)


def test_output_rejects_ancestors_and_outside_paths(fake_repo):
    repo, sentinel = fake_repo
    for candidate in (repo.parent, repo.parent / "elsewhere", repo):
        with pytest.raises(UnsafeOutputPathError):
            resolve_output_dir(candidate, repo_root_path=repo)
    _assert_sentinel(sentinel)


def test_output_rejects_filesystem_root(fake_repo):
    repo, sentinel = fake_repo
    with pytest.raises(UnsafeOutputPathError):
        resolve_output_dir(repo.anchor, repo_root_path=repo)
    _assert_sentinel(sentinel)


def test_publish_only_allows_repository_demo(fake_repo):
    repo, sentinel = fake_repo
    assert resolve_publish_dir(repo / "demo", repo_root_path=repo) == (repo / "demo").resolve()
    for candidate in (repo, repo / "demo" / "corpus", repo / ".tmp" / "out", repo.parent):
        with pytest.raises(UnsafePublishPathError):
            resolve_publish_dir(candidate, repo_root_path=repo)
    _assert_sentinel(sentinel)


def test_output_and_publish_must_be_disjoint(fake_repo):
    repo, sentinel = fake_repo
    output = (repo / ".tmp" / "out").resolve()
    assert_disjoint(output, (repo / "demo").resolve())
    with pytest.raises(UnsafeOutputPathError):
        assert_disjoint(output, output)
    with pytest.raises(UnsafeOutputPathError):
        assert_disjoint(output, output / "nested")
    _assert_sentinel(sentinel)


def test_build_dataset_refuses_non_empty_output_without_deleting(tmp_path):
    target = tmp_path / "generated"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text(SENTINEL_CONTENT, encoding="utf-8")
    before = sorted(path.name for path in target.iterdir())

    with pytest.raises(OutputDirectoryNotEmptyError):
        generate(target)

    _assert_sentinel(sentinel)
    assert sorted(path.name for path in target.iterdir()) == before


def test_build_dataset_refuses_repository_root_without_deleting(fake_repo):
    repo, sentinel = fake_repo
    with pytest.raises(OutputDirectoryNotEmptyError):
        generate(repo)
    _assert_sentinel(sentinel)
    assert (repo / "scripts").is_dir()


def test_cli_rejects_unsafe_output_and_keeps_sentinel(fake_repo):
    import generate_demo_corpus

    repo, sentinel = fake_repo
    for candidate in (repo, repo / "demo", repo / "demo" / "corpus", repo / "scripts", repo / ".tmp"):
        code = generate_demo_corpus.main(
            ["--output", str(candidate), "--font", str(FONT_PATH)],
            repo_root_path=repo,
        )
        assert code == 2
        _assert_sentinel(sentinel)
        assert not (repo / "manifest.json").exists()


def test_cli_rejects_unsafe_publish_and_keeps_sentinel(fake_repo):
    import generate_demo_corpus

    repo, sentinel = fake_repo
    code = generate_demo_corpus.main(
        ["--output", str(repo / ".tmp" / "out"), "--publish", str(repo), "--font", str(FONT_PATH)],
        repo_root_path=repo,
    )
    assert code == 2
    _assert_sentinel(sentinel)
    assert not (repo / ".tmp" / "out").exists()


def test_cli_requires_explicit_output(fake_repo):
    import generate_demo_corpus

    repo, sentinel = fake_repo
    with pytest.raises(SystemExit) as error:
        generate_demo_corpus.main(["--font", str(FONT_PATH)], repo_root_path=repo)
    assert error.value.code == 2
    _assert_sentinel(sentinel)
