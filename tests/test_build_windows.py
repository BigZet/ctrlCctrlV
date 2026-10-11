from pathlib import Path

from scripts.build_windows import publish_archive


def test_open_archive_is_preserved_and_new_artifact_path_is_returned(tmp_path, monkeypatch):
    old = tmp_path / "release.zip"
    new = tmp_path / "staged.zip"
    old.write_bytes(b"currently open archive")
    new.write_bytes(b"new verified archive")
    replace = Path.replace
    def locked_replace(source, target):
        if Path(target) == old:
            raise PermissionError("archive is open")
        return replace(source, target)
    monkeypatch.setattr(Path, "replace", locked_replace)
    published = publish_archive(new, old)
    assert published != old and published.parent == old.parent
    assert published.read_bytes() == b"new verified archive"
    assert old.read_bytes() == b"currently open archive"
    assert not new.exists()
