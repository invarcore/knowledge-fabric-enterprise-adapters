from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from enterprise_adapters.standard_source_adapters import (
    AllowlistedDocumentSourceAdapter,
    AllowlistedWebsiteSourceAdapter,
)


def test_document_adapter_lists_common_file_types(tmp_path: Path) -> None:
    source_root = tmp_path / "approved"
    allowed_root = source_root / "docs"
    allowed_root.mkdir(parents=True)
    (allowed_root / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
    (allowed_root / "beta.txt").write_text("Beta\n", encoding="utf-8")
    _write_docx(allowed_root / "notes.docx", ["Docx line 1", "Docx line 2"])
    (allowed_root / "page.html").write_text("<html><body><h1>Page</h1></body></html>", encoding="utf-8")
    (allowed_root / "ignore.bin").write_bytes(b"\x00")

    adapter = AllowlistedDocumentSourceAdapter(source_root=source_root, allowed_roots=[allowed_root])

    resource_ids = [resource.resource_id for resource in adapter.list_resources()]

    assert resource_ids == [
        "docs/alpha.md",
        "docs/beta.txt",
        "docs/notes.docx",
        "docs/page.html",
    ]


def test_document_adapter_extracts_docx_text(tmp_path: Path) -> None:
    source_root = tmp_path / "approved"
    allowed_root = source_root / "docs"
    allowed_root.mkdir(parents=True)
    docx_path = allowed_root / "guide.docx"
    _write_docx(docx_path, ["Hello", "World"])

    adapter = AllowlistedDocumentSourceAdapter(source_root=source_root, allowed_roots=[allowed_root])
    payload = adapter.fetch_resource("docs/guide.docx")

    assert payload["metadata"]["document_type"] == "docx"
    assert "Hello" in payload["content"]
    assert "World" in payload["content"]


def test_document_adapter_extracts_pdf_text_with_reader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_root = tmp_path / "approved"
    allowed_root = source_root / "docs"
    allowed_root.mkdir(parents=True)
    pdf_path = allowed_root / "guide.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    class _FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _FakeReader:
        pages = [_FakePage("First page"), _FakePage("Second page")]

    monkeypatch.setattr(
        "enterprise_adapters.standard_source_adapters._load_pdf_reader",
        lambda _path: _FakeReader(),
    )

    adapter = AllowlistedDocumentSourceAdapter(source_root=source_root, allowed_roots=[allowed_root])
    payload = adapter.fetch_resource("docs/guide.pdf")

    assert payload["metadata"]["document_type"] == "pdf"
    assert "First page" in payload["content"]
    assert "Second page" in payload["content"]
    assert payload["metadata"]["pages"] == [
        {"page_number": 1, "char_offset": 0, "char_count": 10},
        {"page_number": 2, "char_offset": 11, "char_count": 11},
    ]


def test_document_adapter_sanitizes_credentials(tmp_path: Path) -> None:
    source_root = tmp_path / "approved"
    allowed_root = source_root / "docs"
    allowed_root.mkdir(parents=True)
    secret_doc = allowed_root / "secret.md"
    secret_doc.write_text(
        "# Runbook\nDeploy using AKIAIOSFODNN7EXAMPLE and database postgresql://admin:secret123@db:5432/app",
        encoding="utf-8",
    )

    adapter = AllowlistedDocumentSourceAdapter(source_root=source_root, allowed_roots=[allowed_root])
    payload = adapter.fetch_resource("docs/secret.md")

    assert "AKIAIOSFODNN7EXAMPLE" not in payload["content"]
    assert "[REDACTED_AWS_KEY]" in payload["content"]
    assert "secret123" not in payload["content"]
    assert "postgresql://admin:[REDACTED]@db:5432/app" in payload["content"]


def test_document_adapter_rejects_unapproved_path(tmp_path: Path) -> None:
    source_root = tmp_path / "approved"
    allowed_root = source_root / "docs"
    other_root = source_root / "other"
    allowed_root.mkdir(parents=True)
    other_root.mkdir(parents=True)
    (other_root / "hidden.txt").write_text("hidden", encoding="utf-8")

    adapter = AllowlistedDocumentSourceAdapter(source_root=source_root, allowed_roots=[allowed_root])

    with pytest.raises(ValueError, match="not under an approved allowlisted root"):
        adapter.fetch_resource("other/hidden.txt")


def test_website_adapter_fetches_allowed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><h1>Kubernetes</h1><p>Pods are the smallest deployable unit.</p></body></html>"

    class _FakeHeaders:
        def get_content_type(self) -> str:
            return "text/html"

        def get_content_charset(self) -> str:
            return "utf-8"

    class _FakeResponse:
        headers = _FakeHeaders()

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return html.encode("utf-8")

    monkeypatch.setattr("enterprise_adapters.standard_source_adapters.urlopen", lambda *_args, **_kwargs: _FakeResponse())

    adapter = AllowlistedWebsiteSourceAdapter(
        allowed_urls=["https://kubernetes.io/docs/concepts/workloads/pods/"],
    )
    payload = adapter.fetch_resource("https://kubernetes.io/docs/concepts/workloads/pods/")

    assert payload["metadata"]["host"] == "kubernetes.io"
    assert "Kubernetes" in payload["content"]
    assert "Pods are the smallest deployable unit." in payload["content"]


def test_website_adapter_rejects_unapproved_url() -> None:
    adapter = AllowlistedWebsiteSourceAdapter(
        allowed_urls=["https://kubernetes.io/docs/concepts/workloads/pods/"],
    )

    with pytest.raises(ValueError, match="not in the website allowlist"):
        adapter.fetch_resource("https://example.com/")


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
