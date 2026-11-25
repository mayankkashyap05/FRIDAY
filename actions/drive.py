"""actions/drive.py — Google Drive: list, search, read, upload, download, manage."""

import io
import re
from pathlib import Path

import google_oauth

TEXT_MIME = {
    "text/plain", "text/html", "text/csv", "text/markdown",
    "application/rtf", "application/json", "application/xml",
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
}
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
PDF_MIME = "application/pdf"
MAX_READ_CHARS = 4000


def _service():
    creds = google_oauth.get_credentials()
    if not creds:
        raise RuntimeError("Google is not authorized yet. Run 'google_auth' first.")
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds)


def _escape_drive_q(val: str) -> str:
    """Escape a Drive query string literal (backslash first, then quote)."""
    return str(val).replace("\\", "\\\\").replace("'", "\\'")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _not_trashed(extra: str = "") -> str:
    clause = "trashed = false"
    return f"{clause} and ({extra})" if extra else clause


def _size(file: dict) -> str:
    size = file.get("size")
    return f"{int(size) / 1024:.0f}KB" if size else ""


def _download_dir() -> Path:
    target = Path.home() / "Downloads" / "Friday"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_download_path(name: str) -> Path:
    """Reject absolute paths and traversal; always stay under Downloads/Friday."""
    clean = re.sub(r"[\\/]+", "_", str(name or "download")).strip(" .")
    clean = re.sub(r"\.\.+", "_", clean).strip(" .") or "download"
    return (_download_dir() / clean).resolve()


def _read_file(svc, file_id: str, meta: dict, params: dict) -> str:
    mime = meta.get("mimeType", "")
    name = meta.get("name", "Drive file")
    limit = min(MAX_READ_CHARS, max(1, int(params.get("max_chars", MAX_READ_CHARS) or MAX_READ_CHARS)))
    try:
        if mime == GOOGLE_DOC:
            export_mime = "text/html" if str(params.get("format", "text")).lower() == "html" else "text/plain"
            data = svc.files().export(fileId=file_id, mimeType=export_mime).execute()
        elif mime == GOOGLE_SHEET:
            export_mime = "text/csv" if str(params.get("format", "csv")).lower() != "html" else "text/html"
            data = svc.files().export(fileId=file_id, mimeType=export_mime).execute()
        elif mime == GOOGLE_SLIDES:
            data = svc.files().export(fileId=file_id, mimeType="text/plain").execute()
        elif mime == PDF_MIME:
            from pypdf import PdfReader
            from googleapiclient.http import MediaIoBaseDownload
            request = svc.files().get_media(fileId=file_id)
            stream = io.BytesIO()
            downloader = MediaIoBaseDownload(stream, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            stream.seek(0)
            reader = PdfReader(stream)
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:30])
            return f"📄 {name}:\n" + text[:limit]
        elif mime in TEXT_MIME or mime.startswith("text/"):
            data = svc.files().get_media(fileId=file_id).execute()
        else:
            return (f"📄 {name} ({mime}). Not a readable text file — use 'download' "
                    "to save it on this PC.")
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        return f"📄 {name}:\n" + text[:limit]
    except Exception as exc:
        return f"Could not read Drive file: {exc}"


def drive_action(parameters: dict, response=None, player=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "list")).lower().strip()
    try:
        svc = _service()

        if action in ("list", "recent"):
            query = str(params.get("query", "")).strip()
            extra = f"name contains '{_escape_drive_q(query)}'" if query else ""
            try:
                page_size = max(1, min(100, int(params.get("max_results", 10) or 10)))
            except (TypeError, ValueError):
                page_size = 10
            results = svc.files().list(
                q=_not_trashed(extra), pageSize=page_size,
                fields="files(id,name,mimeType,modifiedTime,size)",
                orderBy="modifiedTime desc",
            ).execute().get("files", [])
            if not results:
                return "No files found on Drive, sir."
            lines = ["📁 Drive files:"]
            for file in results:
                kind = file["mimeType"].split(".")[-1]
                lines.append(f"• {file.get('name')}  ({kind}) {_size(file)}")
            return "\n".join(lines)

        if action == "search":
            query = str(params.get("query", "")).strip()
            if not query:
                return "I need a search query, sir."
            results = svc.files().list(
                q=_not_trashed(f"name contains '{_escape_drive_q(query)}'"),
                pageSize=20, fields="files(id,name,mimeType,size)",
            ).execute().get("files", [])
            if not results:
                return f"No Drive files matching '{query}', sir."
            return "\n".join([f"Results for '{query}':"] +
                             [f"• {f.get('name')}  (id: {f.get('id')})" for f in results])

        if action in ("info", "details", "metadata"):
            file_id = str(params.get("file_id", "")).strip()
            if not file_id:
                return "I need a file_id, sir."
            meta = svc.files().get(
                fileId=file_id,
                fields="id,name,mimeType,size,modifiedTime,parents,webViewLink",
            ).execute()
            return ("Drive file: " + ", ".join(
                f"{key}={value}" for key, value in meta.items() if value is not None))

        if action in ("read", "view", "open", "export"):
            file_id = str(params.get("file_id", "")).strip()
            if not file_id:
                return "I need file_id, sir. Use 'drive search' to find it."
            meta = svc.files().get(fileId=file_id, fields="name,mimeType").execute()
            return _read_file(svc, file_id, meta, params)

        if action in ("upload", "create", "new"):
            from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
            name = str(params.get("name", "")).strip()
            content = str(params.get("content", ""))
            local_path = str(params.get("path", params.get("file_path", ""))).strip()
            if not local_path and not name:
                return "I need a file name or local path, sir."
            parents = []
            folder_id = str(params.get("folder_id", "")).strip()
            if folder_id:
                parents = [folder_id]
            body = {"name": name or Path(local_path).name}
            if parents:
                body["parents"] = parents
            if local_path:
                path = Path(local_path).expanduser()
                if not path.is_file():
                    return f"Local file not found: {path}"
                media = MediaFileUpload(str(path), resumable=False)
            else:
                if not content:
                    return "I need the file content, sir."
                media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")),
                                          mimetype="text/plain", resumable=False)
                body["mimeType"] = "text/plain"
            created = svc.files().create(body=body, media_body=media,
                                         fields="id,name").execute()
            return (f'✅ Uploaded "{created.get("name")}" to Drive '
                    f'(id: {created.get("id")}).')

        if action in ("download", "save"):
            from googleapiclient.http import MediaIoBaseDownload
            file_id = str(params.get("file_id", "")).strip()
            if not file_id:
                return "I need a file_id, sir."
            meta = svc.files().get(fileId=file_id, fields="name,mimeType").execute()
            mime = meta.get("mimeType", "")
            export_mime = {
                GOOGLE_DOC: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                GOOGLE_SHEET: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                GOOGLE_SLIDES: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            }.get(mime)
            request = (svc.files().export(fileId=file_id, mimeType=export_mime)
                       if export_mime else svc.files().get_media(fileId=file_id))
            suffix = {GOOGLE_DOC: ".docx", GOOGLE_SHEET: ".xlsx",
                      GOOGLE_SLIDES: ".pptx"}.get(mime, "")
            name = str(meta.get("name") or "download")
            target = _safe_download_path(name if suffix and name.endswith(suffix) else name + suffix)
            stream = io.BytesIO()
            downloader = MediaIoBaseDownload(stream, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            target.write_bytes(stream.getvalue())
            return f"Downloaded to {target}"

        if action in ("create_folder", "new_folder", "mkdir"):
            name = str(params.get("name", "")).strip()
            if not name:
                return "I need a folder name, sir."
            body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
            folder_id = str(params.get("folder_id", "")).strip()
            if folder_id:
                body["parents"] = [folder_id]
            folder = svc.files().create(body=body, fields="id,name").execute()
            return f"Folder created: {folder.get('name')} (id: {folder.get('id')})."

        if action in ("trash", "delete", "remove"):
            file_id = str(params.get("file_id", "")).strip()
            if not file_id:
                return "I need a file_id, sir."
            svc.files().update(fileId=file_id, body={"trashed": True}).execute()
            verb = "trashed" if action == "trash" else "deleted"
            return f"Drive file {verb}: {file_id}."

        return ("Unknown drive action: " + action +
                ". Use list | search | read | info | upload | download | "
                "create_folder | trash.")
    except Exception as exc:
        return f"Drive error: {exc}"
