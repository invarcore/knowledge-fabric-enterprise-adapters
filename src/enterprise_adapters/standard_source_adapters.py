"""Built-in public source adapters for common document and web inputs.

These adapters are intended for public adopters who want a safe starting point for
ingesting common enterprise content types without writing a custom adapter first.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from enterprise_adapters.content_sanitizer import sanitize_document_content
from enterprise_adapters.contracts import ReadOnlyResource

_DOCUMENT_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf"}
_GITHUB_TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".java",
    ".go",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".csv",
    ".sh",
    ".rb",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".tf",
    ".ini",
    ".toml",
    ".conf",
    ".properties",
}
_WEB_MIME_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}


@dataclass(slots=True)
class AllowlistedDocumentSourceAdapter:
    """Read-only adapter for approved document roots.

    Supported file types:
        - Markdown (.md, .markdown)
        - Plain text (.txt)
        - HTML (.html, .htm)
        - Word (.docx)
        - PDF (.pdf) via optional pypdf extra
    """

    source_root: Path
    allowed_roots: list[Path]

    def __post_init__(self) -> None:
        self.source_root = self.source_root.expanduser().resolve()
        self.allowed_roots = [path.expanduser().resolve() for path in self.allowed_roots]
        if not self.allowed_roots:
            raise ValueError("allowed_roots must not be empty")

    def list_resources(self) -> list[ReadOnlyResource]:
        resources: list[ReadOnlyResource] = []
        for root in self._active_roots():
            for file_path in sorted(root.rglob("*")):
                if file_path.is_file() and file_path.suffix.lower() in _DOCUMENT_EXTENSIONS:
                    resources.append(self._resource_from_path(file_path))
        return resources

    def fetch_resource(self, resource_id: str) -> dict[str, object]:
        file_path = self._resolve_resource_id(resource_id)
        if not self._is_allowed_path(file_path):
            raise ValueError("resource_id is not under an approved allowlisted root")
        if file_path.suffix.lower() not in _DOCUMENT_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {file_path.suffix}")

        pages_meta: list[dict[str, object]] | None = None
        if file_path.suffix.lower() == ".pdf":
            content, pages_meta = _extract_pdf_with_pages(file_path)
        else:
            content = _extract_document_content(file_path)

        content = sanitize_document_content(content)
        stat = file_path.stat()
        metadata: dict[str, object] = {
            "name": file_path.name,
            "extension": file_path.suffix.lower(),
            "document_type": _document_type(file_path.suffix.lower()),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            "source_root": str(self.source_root),
        }
        if pages_meta is not None:
            metadata["pages"] = pages_meta
            metadata["page_count"] = len(pages_meta)

        return {
            "resource_id": resource_id,
            "path": str(file_path),
            "relative_path": file_path.relative_to(self.source_root).as_posix(),
            "content": content,
            "metadata": metadata,
        }

    def _active_roots(self) -> Iterable[Path]:
        for root in self.allowed_roots:
            if self._is_under(root, self.source_root):
                yield root

    def _resolve_resource_id(self, resource_id: str) -> Path:
        candidate = (self.source_root / resource_id).expanduser().resolve()
        if not self._is_under(candidate, self.source_root):
            raise ValueError("resource_id escapes the approved source root")
        if not candidate.exists():
            raise FileNotFoundError(f"Resource not found: {resource_id}")
        return candidate

    def _is_allowed_path(self, candidate: Path) -> bool:
        return any(self._is_under(candidate, root) for root in self.allowed_roots)

    @staticmethod
    def _is_under(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def _resource_from_path(self, file_path: Path) -> ReadOnlyResource:
        stat = file_path.stat()
        return ReadOnlyResource(
            resource_id=file_path.relative_to(self.source_root).as_posix(),
            name=file_path.stem,
            metadata={
                "path": str(file_path),
                "relative_path": file_path.relative_to(self.source_root).as_posix(),
                "extension": file_path.suffix.lower(),
                "document_type": _document_type(file_path.suffix.lower()),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            },
        )


@dataclass(slots=True)
class AllowlistedWebsiteSourceAdapter:
    """Read-only adapter for approved live web pages.

    The adapter is exact-URL allowlisted: only URLs listed in ``allowed_urls`` may be
    fetched. This keeps the public adapter safe and predictable for adopters.
    """

    allowed_urls: list[str]
    user_agent: str = "knowledge-fabric-enterprise-adapters/1.0"

    def __post_init__(self) -> None:
        self.allowed_urls = [_normalize_url(url) for url in self.allowed_urls]
        if not self.allowed_urls:
            raise ValueError("allowed_urls must not be empty")

    def list_resources(self) -> list[ReadOnlyResource]:
        return [
            ReadOnlyResource(
                resource_id=url,
                name=_url_name(url),
                metadata={
                    "url": url,
                    "scheme": urlparse(url).scheme,
                    "host": urlparse(url).netloc,
                },
            )
            for url in sorted(self.allowed_urls)
        ]

    def fetch_resource(self, resource_id: str) -> dict[str, object]:
        url = _normalize_url(resource_id)
        if url not in self.allowed_urls:
            raise ValueError("resource_id is not in the website allowlist")

        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "text/html,text/plain"})
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"

        text = raw.decode(charset, errors="replace")
        normalized = sanitize_document_content(_extract_web_content(text, content_type))
        parsed = urlparse(url)
        return {
            "resource_id": url,
            "path": url,
            "relative_path": parsed.path or "/",
            "content": normalized,
            "metadata": {
                "url": url,
                "host": parsed.netloc,
                "scheme": parsed.scheme,
                "content_type": content_type,
                "fetched_at": datetime.now(UTC).isoformat(),
            },
        }


@dataclass(slots=True)
class GitHubRepositorySourceAdapter:
    """Read-only adapter for approved GitHub repositories and paths.

    Supported usage:
        - ingest public or private GitHub repositories
        - allowlist one or more path prefixes, for example: ["docs", "content/en/docs"]
        - optionally use a GitHub token for private repos or higher rate limits

    The adapter fetches file content via the GitHub REST API and normalizes it into the
    same read-only shape used by the other source adapters.
    """

    repository: str
    allowed_paths: list[str]
    ref: str = "main"
    token: str = ""
    api_base_url: str = "https://api.github.com"

    def __post_init__(self) -> None:
        if "/" not in self.repository.strip("/"):
            raise ValueError("repository must be in owner/repo format")
        self.allowed_paths = [_normalize_repo_path(path) for path in self.allowed_paths]
        if not self.allowed_paths:
            raise ValueError("allowed_paths must not be empty")
        self.api_base_url = self.api_base_url.rstrip("/")

    @classmethod
    def from_env(
        cls,
        *,
        repository: str | None = None,
        allowed_paths: list[str] | None = None,
        ref: str | None = None,
        token: str | None = None,
    ) -> "GitHubRepositorySourceAdapter":
        import os

        repo = repository or os.environ.get("GITHUB_SOURCE_REPOSITORY", "")
        if not repo:
            raise EnvironmentError("GITHUB_SOURCE_REPOSITORY environment variable is required")
        paths = allowed_paths or _split_csv(os.environ.get("GITHUB_SOURCE_ALLOWLIST", ""))
        if not paths:
            raise EnvironmentError("allowed_paths or GITHUB_SOURCE_ALLOWLIST must not be empty")
        return cls(
            repository=repo,
            allowed_paths=paths,
            ref=ref or os.environ.get("GITHUB_SOURCE_REF", "main"),
            token=token if token is not None else os.environ.get("GITHUB_TOKEN", ""),
            api_base_url=os.environ.get("GITHUB_API_BASE_URL", "https://api.github.com"),
        )

    def list_resources(self) -> list[ReadOnlyResource]:
        resources: list[ReadOnlyResource] = []
        for entry in self._list_tree_entries():
            path = entry["path"]
            if entry.get("type") != "blob":
                continue
            if not self._is_allowed_path(path):
                continue
            if Path(path).suffix.lower() not in _GITHUB_TEXT_EXTENSIONS:
                continue
            resources.append(
                ReadOnlyResource(
                    resource_id=path,
                    name=Path(path).stem,
                    metadata={
                        "repository": self.repository,
                        "path": path,
                        "extension": Path(path).suffix.lower(),
                        "size_bytes": entry.get("size", 0),
                        "ref": self.ref,
                    },
                )
            )
        return sorted(resources, key=lambda item: item.resource_id)

    def fetch_resource(self, resource_id: str) -> dict[str, object]:
        path = _normalize_repo_path(resource_id)
        if not self._is_allowed_path(path):
            raise ValueError("resource_id is not under an approved allowlisted path")

        payload = self._request_json(
            f"/repos/{self.repository}/contents/{quote(path)}?ref={quote(self.ref)}"
        )
        if isinstance(payload, list):
            raise ValueError("resource_id resolved to a directory, not a file")

        encoding = payload.get("encoding", "")
        content = payload.get("content", "")
        if encoding == "base64":
            raw = base64.b64decode(str(content).encode("utf-8"))
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(content)

        normalized = sanitize_document_content(_normalize_text(text))
        return {
            "resource_id": path,
            "path": path,
            "relative_path": path,
            "content": normalized,
            "metadata": {
                "repository": self.repository,
                "path": path,
                "ref": self.ref,
                "extension": Path(path).suffix.lower(),
                "source": payload.get("html_url", ""),
                "sha": payload.get("sha", ""),
                "size_bytes": payload.get("size", 0),
                "fetched_at": datetime.now(UTC).isoformat(),
            },
        }

    def _list_tree_entries(self) -> list[dict[str, object]]:
        payload = self._request_json(
            f"/repos/{self.repository}/git/trees/{quote(self.ref)}?recursive=1"
        )
        tree = payload.get("tree", [])
        if not isinstance(tree, list):
            raise ValueError("Unexpected GitHub tree response")
        return [entry for entry in tree if isinstance(entry, dict) and "path" in entry]

    def _request_json(self, path: str) -> dict[str, object]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "knowledge-fabric-enterprise-adapters/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"{self.api_base_url}{path}", headers=headers)
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _is_allowed_path(self, candidate: str) -> bool:
        normalized = _normalize_repo_path(candidate)
        return any(_is_under_repo_path(normalized, allowed) for allowed in self.allowed_paths)


def _document_type(extension: str) -> str:
    if extension in {".md", ".markdown"}:
        return "markdown"
    if extension == ".txt":
        return "text"
    if extension in {".html", ".htm"}:
        return "html"
    if extension == ".docx":
        return "docx"
    if extension == ".pdf":
        return "pdf"
    return "unknown"


def _extract_document_content(file_path: Path) -> str:
    extension = file_path.suffix.lower()
    if extension in {".md", ".markdown", ".txt"}:
        return _normalize_text(file_path.read_bytes().decode("utf-8"))
    if extension in {".html", ".htm"}:
        return _extract_web_content(file_path.read_bytes().decode("utf-8"), "text/html")
    if extension == ".docx":
        return _extract_docx_text(file_path)
    if extension == ".pdf":
        return _extract_pdf_text(file_path)
    raise ValueError(f"Unsupported file type: {extension}")


def _extract_docx_text(file_path: Path) -> str:
    with ZipFile(file_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        runs = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        text = "".join(runs).strip()
        if text:
            paragraphs.append(text)
    return _normalize_text("\n".join(paragraphs))


def _extract_pdf_with_pages(file_path: Path) -> tuple[str, list[dict[str, object]]]:
    reader = _load_pdf_reader(file_path)
    pages_meta: list[dict[str, object]] = []
    full_text_parts: list[str] = []
    current_offset = 0
    for i, page in enumerate(reader.pages, start=1):
        extracted = page.extract_text() or ""
        if extracted:
            pages_meta.append({
                "page_number": i,
                "char_offset": current_offset,
                "char_count": len(extracted),
            })
            full_text_parts.append(extracted)
            current_offset += len(extracted) + 1
    return _normalize_text("\n".join(full_text_parts)), pages_meta


def _extract_pdf_text(file_path: Path) -> str:
    text, _ = _extract_pdf_with_pages(file_path)
    return text


def _load_pdf_reader(file_path: Path):
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - exercised in docs, not tests
        raise RuntimeError(
            "PDF support requires the optional 'pypdf' dependency. Install with: pip install -e \".[pdf]\""
        ) from exc
    return PdfReader(str(file_path))


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in {"p", "br", "div", "li", "section", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return _normalize_text("".join(self._parts))


def _extract_web_content(text: str, content_type: str) -> str:
    if content_type in _WEB_MIME_TYPES:
        parser = _HTMLTextExtractor()
        parser.feed(text)
        return parser.get_text()
    return _normalize_text(text)


def _normalize_text(text: str) -> str:
    text = text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized = "\n".join(lines).strip()
    return normalized + ("\n" if normalized else "")


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    normalized_path = parsed.path or "/"
    return parsed._replace(path=normalized_path, fragment="").geturl()


def _url_name(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path.rsplit("/", 1)[-1] or parsed.netloc


def _normalize_repo_path(path: str) -> str:
    normalized = Path(path).as_posix().strip("/")
    if normalized in {"", "."}:
        return ""
    return normalized


def _is_under_repo_path(candidate: str, allowed: str) -> bool:
    if allowed == "":
        return True
    if candidate == allowed:
        return True
    return candidate.startswith(f"{allowed}/")


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
