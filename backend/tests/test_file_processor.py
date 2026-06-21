"""Срез 4 — file processor routing (CSV pure; image -> vision mocked)."""
from __future__ import annotations


async def test_csv_bytes_decoded_to_text():
    from services.file_processor import process_file

    raw = "type,amount,currency\ncash,1000,EUR\n".encode("utf-8")
    text = await process_file(raw, mime_type="text/csv", filename="p.csv")
    assert "cash" in text and "EUR" in text


async def test_image_routes_to_vision(monkeypatch):
    import services.file_processor as fp

    class _FakeMsg:
        content = "cash 1000 EUR в Минске"

    class _FakeModel:
        async def ainvoke(self, _messages):
            return _FakeMsg()

    monkeypatch.setattr(fp, "get_model", lambda *a, **k: _FakeModel())
    # Exercise the real (mocked-model) vision path, not the demo short-circuit.
    monkeypatch.setattr(fp.settings, "anthropic_api_key", "test-key")

    text = await fp.process_file(b"\x89PNG\r\n\x1a\n", mime_type="image/png", filename="s.png")
    assert text == "cash 1000 EUR в Минске"
