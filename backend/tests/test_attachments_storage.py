"""Receipt storage (fin-02): the local-disk backend, byte encryption, the signature sniffer
and the UPLOADS_DIR setting."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from core import crypto
from core.config import Settings
from core.storage import (
    InvalidStorageKey,
    LocalFsStorage,
    StorageBackend,
    get_storage,
    remove_files,
    remove_tree,
)
from services.attachments import sniff_mime
from tests.attachment_client import GIF, HEIC, PDF, jpeg, png, webp

BACKEND = Path(__file__).resolve().parent.parent
USER = "a1a1a1a1-1111-4111-8111-1111111111a1"  # has letters: its upper-case form is another string
EXPENSE = "22222222-2222-4222-8222-222222222222"
ATTACHMENT = "33333333-3333-4333-8333-333333333333"
OTHER_ATTACHMENT = "44444444-4444-4444-8444-444444444444"
OTHER_USER = "55555555-5555-4555-8555-555555555555"
KEY = f"u/{USER}/{EXPENSE}/{ATTACHMENT}.jpg"
OTHER_KEY = f"u/{USER}/{EXPENSE}/{OTHER_ATTACHMENT}.png"


@pytest.fixture
def storage(tmp_path) -> LocalFsStorage:
    return LocalFsStorage(tmp_path / "root")


# --- the backend ------------------------------------------------------------------------


def test_local_storage_is_a_storage_backend(storage):
    assert isinstance(storage, StorageBackend)


def test_put_then_get_returns_the_same_bytes(storage):
    storage.put(KEY, b"\x00\x01binary\xff")

    assert storage.get(KEY) == b"\x00\x01binary\xff"


def test_put_accepts_a_bytearray_without_a_copy_on_the_caller_side(storage):
    storage.put(KEY, bytearray(b"buffer"))

    assert storage.get(KEY) == b"buffer"


def test_put_creates_missing_directories_and_replaces_an_existing_file(storage, tmp_path):
    storage.put(KEY, b"first")
    storage.put(KEY, b"second")

    assert storage.get(KEY) == b"second"
    assert (tmp_path / "root" / KEY).is_file()


def test_get_of_a_missing_key_raises_file_not_found(storage):
    with pytest.raises(FileNotFoundError):
        storage.get(KEY)


def test_delete_removes_the_file_and_is_idempotent(storage, tmp_path):
    storage.put(KEY, b"x")

    storage.delete(KEY)
    storage.delete(KEY)  # already gone: not an error

    assert not (tmp_path / "root" / KEY).exists()


def test_delete_prunes_empty_parent_directories_but_keeps_the_root(storage, tmp_path):
    storage.put(KEY, b"x")

    storage.delete(KEY)

    root = tmp_path / "root"
    assert root.is_dir() and list(root.iterdir()) == []


def test_delete_keeps_a_directory_that_still_holds_another_file(storage):
    storage.put(KEY, b"x")
    storage.put(OTHER_KEY, b"y")

    storage.delete(KEY)

    assert storage.get(OTHER_KEY) == b"y"


# --- keys: exactly u/<uuid>/<uuid>/<uuid>.(jpg|png|webp) -----------------------------------


@pytest.mark.parametrize("ext", ["jpg", "png", "webp"])
def test_the_three_image_extensions_are_valid_keys(storage, ext):
    storage.put(f"u/{USER}/{EXPENSE}/{ATTACHMENT}.{ext}", b"x")

    assert storage.get(f"u/{USER}/{EXPENSE}/{ATTACHMENT}.{ext}") == b"x"


@pytest.mark.parametrize(
    "bad_key",
    [
        "../evil.jpg",
        "u/../../evil.jpg",
        "u/../evil.jpg",
        "/etc/passwd",
        "C:/Windows/evil.jpg",
        "u\\..\\evil.jpg",
        "u/./x.jpg",
        "u//x.jpg",
        "",
        ".",
        "..",
        "u/x\x00.jpg",
        "u/ключ.jpg",
        "u/a b.jpg",
        # right alphabet, wrong shape
        "u/x/y/z.jpg",
        f"u/{USER}/{EXPENSE}/{ATTACHMENT}",  # no extension
        f"u/{USER}/{EXPENSE}/{ATTACHMENT}.gif",
        f"u/{USER}/{EXPENSE}/{ATTACHMENT}.JPG",
        f"u/{USER}/{EXPENSE}/{ATTACHMENT}.jpg.exe",
        f"u/{USER}/{EXPENSE}/{ATTACHMENT}.jpg/",
        f"u/{USER}/{EXPENSE}/{ATTACHMENT}.jpg\n",
        f"u/{USER}/{EXPENSE}/extra/{ATTACHMENT}.jpg",
        f"u/{USER}/{ATTACHMENT}.jpg",
        f"x/{USER}/{EXPENSE}/{ATTACHMENT}.jpg",
        f"u/{USER.upper()}/{EXPENSE}/{ATTACHMENT}.jpg",
        f"u/{USER}/{EXPENSE}/{ATTACHMENT[:-1]}.jpg",
        f".u/{USER}/{EXPENSE}/{ATTACHMENT}.jpg",
    ],
)
def test_keys_that_are_not_the_servers_own_shape_are_refused_everywhere(storage, tmp_path, bad_key):
    with pytest.raises(InvalidStorageKey):
        storage.put(bad_key, b"x")
    with pytest.raises(InvalidStorageKey):
        storage.get(bad_key)
    with pytest.raises(InvalidStorageKey):
        storage.delete(bad_key)

    assert not (tmp_path / "evil.jpg").exists()
    assert not (tmp_path / "root").exists() or [p for p in (tmp_path / "root").rglob("*")] == []


def test_a_symlink_inside_the_root_cannot_lead_outside_it(storage, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "root").mkdir()
    try:
        os.symlink(outside, tmp_path / "root" / "u", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available for this user")

    with pytest.raises(InvalidStorageKey):
        storage.put(KEY, b"x")

    assert list(outside.rglob("*")) == []


def test_a_failed_write_leaves_neither_the_file_nor_a_temp_file(storage, tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        storage.put(KEY, b"x")

    assert [p for p in (tmp_path / "root").rglob("*") if p.is_file()] == []


def test_a_successful_write_leaves_no_temp_file(storage, tmp_path):
    storage.put(KEY, b"x")

    assert [p.name for p in (tmp_path / "root").rglob("*") if p.is_file()] == [f"{ATTACHMENT}.jpg"]


def test_get_storage_follows_the_uploads_dir_setting(uploads_dir):
    storage = get_storage()
    storage.put(KEY, b"z")

    assert isinstance(storage, LocalFsStorage)
    assert (uploads_dir / KEY).read_bytes() == b"z"


# --- removing a whole folder (account deletion) ---------------------------------------------


def test_delete_prefix_removes_a_users_whole_tree_including_temp_and_stray_files(storage, tmp_path):
    storage.put(KEY, b"x")
    storage.put(OTHER_KEY, b"y")
    folder = tmp_path / "root" / "u" / USER / EXPENSE
    (folder / f".{ATTACHMENT}.jpg.deadbeef.tmp").write_bytes(b"half written")
    (folder / "stray.txt").write_bytes(b"not ours")
    survivor = f"u/{OTHER_USER}/{EXPENSE}/{ATTACHMENT}.jpg"
    storage.put(survivor, b"keep me")

    storage.delete_prefix(f"u/{USER}")

    assert not (tmp_path / "root" / "u" / USER).exists()
    assert storage.get(survivor) == b"keep me"


def test_delete_prefix_removes_the_last_users_folder_and_leaves_the_root(storage, tmp_path):
    storage.put(KEY, b"x")

    storage.delete_prefix(f"u/{USER}")

    root = tmp_path / "root"
    assert root.is_dir() and list(root.iterdir()) == []


def test_delete_prefix_of_nothing_is_not_an_error(storage):
    storage.delete_prefix(f"u/{USER}")
    storage.delete_prefix(f"u/{USER}/{EXPENSE}")


def test_delete_prefix_takes_a_user_or_an_expense_folder_only(storage):
    storage.put(KEY, b"x")
    storage.put(OTHER_KEY.replace(EXPENSE, OTHER_ATTACHMENT), b"y")

    storage.delete_prefix(f"u/{USER}/{EXPENSE}")

    assert storage.get(OTHER_KEY.replace(EXPENSE, OTHER_ATTACHMENT)) == b"y"
    with pytest.raises(FileNotFoundError):
        storage.get(KEY)


@pytest.mark.parametrize(
    "bad_prefix",
    ["", "u", "u/", "..", "u/..", "../u", f"u/{USER}/", f"u/{USER}/..", "u/not-a-uuid", "/", f"u\\{USER}",
     f"u/{USER}/{EXPENSE}/{ATTACHMENT}.jpg", f"x/{USER}", f"u/{USER.upper()}"],
)
def test_delete_prefix_refuses_anything_but_a_user_or_expense_folder(storage, bad_prefix):
    storage.put(KEY, b"x")

    with pytest.raises(InvalidStorageKey):
        storage.delete_prefix(bad_prefix)

    assert storage.get(KEY) == b"x"


def test_delete_prefix_does_not_follow_a_symlinked_folder_out_of_the_root(storage, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_bytes(b"keep")
    user_folder = tmp_path / "root" / "u" / USER
    user_folder.parent.mkdir(parents=True)
    try:
        os.symlink(outside, user_folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available for this user")

    with pytest.raises(InvalidStorageKey):
        storage.delete_prefix(f"u/{USER}")

    assert (outside / "precious.txt").read_bytes() == b"keep"


def test_remove_files_deletes_all_and_survives_a_failing_backend(storage, caplog):
    storage.put(KEY, b"x")
    storage.put(OTHER_KEY, b"y")

    class Flaky:
        def __init__(self, inner):
            self.inner = inner

        def delete(self, key):
            if key == KEY:
                raise OSError(f"cannot remove {key} at C:\\secret\\path")
            self.inner.delete(key)

    remove_files(Flaky(storage), [KEY, OTHER_KEY])

    assert storage.get(KEY) == b"x"  # the failing one is still there ...
    with pytest.raises(FileNotFoundError):  # ... and the next one was still tried
        storage.get(OTHER_KEY)
    logged = caplog.text
    assert "failed" in logged.lower()
    assert KEY not in logged and "secret" not in logged and USER not in logged


def test_remove_tree_logs_only_the_kind_of_error(caplog):
    class Broken:
        def delete_prefix(self, prefix):
            raise PermissionError(f"denied: C:\\private\\{prefix}")

    remove_tree(Broken(), f"u/{USER}")

    assert "failed" in caplog.text.lower() and "PermissionError" in caplog.text
    assert USER not in caplog.text and "private" not in caplog.text


# --- settings ---------------------------------------------------------------------------


def test_uploads_dir_defaults_to_backend_uploads_whatever_the_cwd():
    assert Settings(_env_file=None).uploads_dir == str(BACKEND / "uploads")


def test_a_blank_uploads_dir_falls_back_to_the_default():
    assert Settings(_env_file=None, uploads_dir="  ").uploads_dir == str(BACKEND / "uploads")


def test_uploads_dir_can_be_set_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "elsewhere"))

    assert Settings(_env_file=None).uploads_dir == str(tmp_path / "elsewhere")


# --- encryption of bytes ----------------------------------------------------------------


def test_bytes_pass_through_unchanged_without_a_key():
    assert crypto.encrypt_bytes(b"\xff\xd8\xffabc") == b"\xff\xd8\xffabc"
    assert crypto.decrypt_bytes(b"\xff\xd8\xffabc") == b"\xff\xd8\xffabc"


def test_bytes_are_encrypted_with_fernet_when_a_key_is_set(fernet_on):
    plain = jpeg("secret-receipt")

    sealed = crypto.encrypt_bytes(plain)

    assert sealed != plain and b"secret-receipt" not in sealed
    assert Fernet(fernet_on.encode()).decrypt(sealed) == plain
    assert crypto.decrypt_bytes(sealed) == plain


def test_a_bytearray_is_sealed_too(fernet_on):
    plain = bytearray(jpeg("buffer"))

    assert crypto.decrypt_bytes(crypto.encrypt_bytes(plain)) == bytes(plain)


def test_bytes_stored_before_encryption_was_enabled_still_read_back(fernet_on):
    legacy = png("legacy")

    assert crypto.decrypt_bytes(legacy) == legacy


# --- the signature sniffer --------------------------------------------------------------

_RIFF_MIN = b"RIFF" + (12).to_bytes(4, "little") + b"WEBP" + b"VP8 " + b"\x00\x00\x00\x00"


def _riff(size_field: int, fourcc: bytes = b"VP8 ", total: int = 40) -> bytes:
    head = b"RIFF" + size_field.to_bytes(4, "little") + b"WEBP" + fourcc
    return head.ljust(total, b"\x00")


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (jpeg(), "image/jpeg"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (png(), "image/png"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (webp(), "image/webp"),
        (bytearray(jpeg()), "image/jpeg"),
        (bytearray(webp()), "image/webp"),
        (_RIFF_MIN, "image/webp"),  # the smallest well-formed container: size field 12
        (_riff(32, b"VP8 "), "image/webp"),  # lossy
        (_riff(32, b"VP8L"), "image/webp"),  # lossless
        (_riff(32, b"VP8X"), "image/webp"),  # extended
        (_riff(20, b"VP8 ", total=60), "image/webp"),  # trailing bytes after the RIFF are ignored
    ],
)
def test_supported_signatures(data, mime):
    assert sniff_mime(data) == mime


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff\xd8",  # truncated JPEG signature
        b"\x89PNG\r\n\x1a",  # truncated PNG signature
        b"\x89PNG\r\n\x1a\x00",  # wrong last byte
        b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 32,  # RIFF, but a sound
        b"RIFF\x00\x00\x00\x00WEB",  # truncated
        b"WEBP" + b"\x00" * 20,  # marker without RIFF
        HEIC,
        GIF,
        PDF,
        b"just a text file renamed to receipt.jpg",
        b"<html><script>alert(1)</script></html>",
        b"\x00" * 64,
        # WEBP: the RIFF header must be consistent with the data and the first chunk known
        b"RIFF\x00\x00\x00\x00WEBP",  # size field 0, no chunk
        _riff(0),  # size field 0
        _riff(11),  # smaller than "WEBP" + one chunk header
        _riff(10_000, total=40),  # claims more bytes than the file has (truncated / lying)
        _riff(0xFFFFFFFF),  # absurd size
        _riff(32, b"ABCD"),  # a RIFF/WEBP shell with a chunk type no WEBP has
        _riff(32, b"WAVE"),
        _riff(32, b"fmt "),
        b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8",  # chunk type cut short
        _RIFF_MIN[:-1],  # one byte short of what its own size field promises
    ],
)
def test_everything_else_has_no_mime(data):
    assert sniff_mime(data) is None
