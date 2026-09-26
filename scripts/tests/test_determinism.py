"""确定性生成验收：相同镜像、相同种子下两次生成必须字节一致。"""

from __future__ import annotations

from conftest import generate, read_ground_truth, read_manifest, relative_files

from demo_corpus import facts


def test_two_generations_produce_identical_files(tmp_path):
    first = generate(tmp_path / "first")
    second = generate(tmp_path / "second")
    root_a, root_b = tmp_path / "first", tmp_path / "second"

    files_a = relative_files(root_a)
    files_b = relative_files(root_b)
    assert files_a == files_b
    assert files_a == [
        "corpus/" + doc["path"].split("/")[-1] for doc in read_manifest(root_a)["documents"]
    ] + ["ground_truth.jsonl", "manifest.json"]

    for relative in files_a:
        assert (root_a / relative).read_bytes() == (root_b / relative).read_bytes(), relative

    assert first["dataset_sha256"] == second["dataset_sha256"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["ground_truth_sha256"] == second["ground_truth_sha256"]


def test_generates_exactly_fifteen_documents(dataset):
    manifest = dataset["manifest"]
    documents = manifest["documents"]
    assert len(documents) == 15

    counts = {"pdf": 0, "docx": 0, "xlsx": 0}
    for doc in documents:
        counts[doc["file_type"]] += 1
    assert counts == {"pdf": 5, "docx": 5, "xlsx": 5}


def test_corpus_directory_has_no_extra_files(dataset):
    root = dataset["root"]
    on_disk = sorted(path.name for path in (root / "corpus").iterdir() if path.is_file())
    registered = sorted(doc["path"].split("/")[-1] for doc in dataset["manifest"]["documents"])
    assert on_disk == registered
    assert ".gitkeep" not in on_disk


def test_manifest_records_fixed_dataset_metadata(dataset):
    manifest = dataset["manifest"]
    assert manifest["dataset_name"] == facts.DATASET_NAME
    assert manifest["dataset_version"] == facts.DATASET_VERSION
    assert manifest["school_name"] == facts.SCHOOL_NAME
    assert manifest["fictional"] is True
    assert manifest["seed"] == facts.DEFAULT_SEED
    assert manifest["generator_version"] == facts.GENERATOR_VERSION
    assert manifest["generated_at"] == facts.GENERATED_AT
    assert manifest["document_count"] == 15


def test_ground_truth_file_is_rewritten_identically(tmp_path):
    generate(tmp_path / "a")
    generate(tmp_path / "b")
    entries_a = read_ground_truth(tmp_path / "a")
    entries_b = read_ground_truth(tmp_path / "b")
    assert entries_a == entries_b
    assert len(entries_a) >= 50
