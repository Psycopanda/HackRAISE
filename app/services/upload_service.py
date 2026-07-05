"""Extract plain text from an uploaded document or audio file.

Supports `.txt` (read as-is), `.pdf` (extracted page by page via pypdf) and
audio files (transcribed through Mistral's audio/transcriptions endpoint).
The result is a plain string meant to seed the system agent's first message —
nothing here is persisted.
"""

from io import BytesIO

from pypdf import PdfReader

from app.services.mistral_service import get_mistral_service

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".aac"}


class UploadTooLargeError(ValueError):
    """Raised when an uploaded file exceeds MAX_UPLOAD_BYTES."""


def _kind_for(filename: str, content_type: str | None) -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()

    if ctype.startswith("audio/") or any(name.endswith(ext) for ext in AUDIO_EXTENSIONS):
        return "audio"
    if ctype == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if ctype.startswith("text/") or name.endswith(".txt"):
        return "text"
    raise ValueError("Format non supporté (.txt, .pdf ou audio uniquement).")


def _extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages).strip()


async def extract_text(filename: str, content_type: str | None, data: bytes) -> dict:
    """Return `{"text", "filename", "kind"}` for a supported upload."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadTooLargeError("Fichier trop volumineux (limite : 20 Mo).")

    kind = _kind_for(filename, content_type)

    if kind == "text":
        text = data.decode("utf-8", errors="replace").strip()
    elif kind == "pdf":
        text = _extract_pdf_text(data)
    else:  # audio
        mistral = get_mistral_service()
        text = await mistral.transcribe_audio(data, filename, content_type)

    return {"text": text, "filename": filename, "kind": kind}
