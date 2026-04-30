from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class ProfileSourceError(ValueError):
    pass


@dataclass(frozen=True)
class ProfileFragment:
    source: str
    text: str
    raw_excerpt_hash: str
    filename: str


class ResumeSource:
    def __init__(self, *, max_bytes: int = 2_000_000) -> None:
        self.max_bytes = max_bytes

    def fetch_bytes(self, filename: str, data: bytes) -> ProfileFragment:
        if len(data) > self.max_bytes:
            raise ProfileSourceError("resume file too large")
        suffix = Path(filename).suffix.lower()
        if suffix == ".txt" or suffix == "":
            text = data.decode("utf8", errors="replace")
        elif suffix == ".pdf":
            text = self._pdf_text(data)
        elif suffix == ".docx":
            text = self._docx_text(data)
        else:
            raise ProfileSourceError(f"unsupported resume extension: {suffix or '(none)'}")
        return ProfileFragment(
            source="resume",
            text=text.strip(),
            raw_excerpt_hash=hashlib.sha256(data[:4096]).hexdigest(),
            filename=Path(filename).name or "resume.txt",
        )

    def _pdf_text(self, data: bytes) -> str:
        try:
            from io import BytesIO

            from pdfminer.high_level import extract_text  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - env dependent
            raise ProfileSourceError("pdf resume parsing requires pdfminer.six") from exc
        return str(extract_text(BytesIO(data)) or "")

    def _docx_text(self, data: bytes) -> str:
        try:
            from io import BytesIO

            import docx  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - env dependent
            raise ProfileSourceError("docx resume parsing requires python-docx") from exc
        document = docx.Document(BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)
