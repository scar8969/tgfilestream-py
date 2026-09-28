"""Unit tests for ID packing/unpacking and file-name extraction."""
import pytest

from tgfilestream.util import get_file_name, pack_id, unpack_id


class FakeFile:
    def __init__(self, name=None, size=10, mime_type="application/octet-stream"):
        self.name = name
        self.size = size
        self.mime_type = mime_type


class FakeMessage:
    def __init__(self, file=None, media=None):
        self.file = file
        self.media = media


class FakeDocAttr:
    def __init__(self, file_name):
        self.file_name = file_name


class FakeDocument:
    def __init__(self, file_name):
        self.attributes = [FakeDocAttr(file_name)]


class FakeMedia:
    def __init__(self, file_name):
        self.document = FakeDocument(file_name)


def test_pack_unpack_roundtrip():
    for peer, msg in [(1001, 1), (123456789, 987654321), (1, 1), (2**31, 2**16)]:
        token = pack_id(peer, msg)
        p, m = unpack_id(token)
        assert (p, m) == (peer, msg)


def test_unpack_invalid():
    assert unpack_id(-1) == (None, None)
    assert unpack_id(2**64) == (None, None)
    assert unpack_id(0) == (None, None)


def test_get_file_name_prefers_message_file():
    msg = FakeMessage(file=FakeFile(name="photo.jpg"))
    assert get_file_name(msg) == "photo.jpg"


def test_get_file_name_falls_back_to_document_attr():
    msg = FakeMessage(media=FakeMedia("archive.zip"))
    assert get_file_name(msg) == "archive.zip"


def test_get_file_name_default():
    assert get_file_name(FakeMessage()) == "file.bin"
