"""Google Drive upload via a service account (fully automated, no browser).

Setup:
  1. Google Cloud Console -> create project -> enable "Google Drive API".
  2. Create a Service Account -> download its JSON key as credentials.json.
  3. Share the target Drive folder with the service-account email (Editor).
  4. Set DRIVE_FOLDER_ID (the id in the folder's URL) in .env.

If credentials or folder id are missing, the agent keeps working and saves
processed images locally instead (the report will say so).
"""

import logging
import os

from .config import Config

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class DriveUploader:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.service = None
        if cfg.dry_run:
            log.info("Dry-run mode: Google Drive upload disabled.")
            return
        if not cfg.drive_folder_id:
            log.warning("DRIVE_FOLDER_ID not set - images will be saved locally only.")
            return
        if not os.path.exists(cfg.drive_credentials):
            log.warning("Drive credentials file %r not found - saving locally only.",
                        cfg.drive_credentials)
            return
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                cfg.drive_credentials, scopes=_SCOPES)
            self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
            log.info("Google Drive upload enabled (folder id: %s).", cfg.drive_folder_id)
        except Exception as e:
            log.error("Failed to initialise Google Drive client: %s", e)
            self.service = None

    @property
    def enabled(self) -> bool:
        return self.service is not None

    def _find_existing(self, name: str) -> str | None:
        q = (f"name = '{name.replace(chr(39), chr(92)+chr(39))}' and "
             f"'{self.cfg.drive_folder_id}' in parents and trashed = false")
        res = self.service.files().list(
            q=q, fields="files(id, name)", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def upload(self, local_path: str, filename: str) -> str:
        """Upload (or update) a file in the target folder. Returns a Drive link."""
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(local_path, mimetype="image/jpeg", resumable=True)
        existing_id = self._find_existing(filename)
        if existing_id:
            f = self.service.files().update(
                fileId=existing_id, media_body=media,
                fields="id, webViewLink", supportsAllDrives=True).execute()
            log.info("Updated existing Drive file %s", f["id"])
        else:
            meta = {"name": filename, "parents": [self.cfg.drive_folder_id]}
            f = self.service.files().create(
                body=meta, media_body=media,
                fields="id, webViewLink", supportsAllDrives=True).execute()
            log.info("Uploaded to Drive: %s", f["id"])
        return f.get("webViewLink") or f"https://drive.google.com/file/d/{f['id']}/view"
