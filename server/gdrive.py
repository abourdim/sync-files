"""
SyncFiles — Google Drive API Integration
OAuth2 authentication, upload, download, delta awareness.
"""

import os
import io
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger('syncfiles.gdrive')

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    HAS_GDRIVE = True
except ImportError:
    HAS_GDRIVE = False

SCOPES = ['https://www.googleapis.com/auth/drive.file']


class GDriveSync:
    """Google Drive sync via API."""

    def __init__(self, cred_store):
        """
        cred_store: CredentialStore instance (unlocked)
        """
        self.cred_store = cred_store
        self._service = None
        self._creds = None

    @staticmethod
    def is_available():
        return HAS_GDRIVE

    def is_configured(self):
        """Check if Google Drive credentials are stored."""
        return self.cred_store.has('gdrive', 'token_json')

    def authorize_interactive(self, client_secrets_path):
        """
        Run OAuth2 browser flow.
        client_secrets_path: path to client_secret.json from Google Cloud Console
        """
        if not HAS_GDRIVE:
            raise RuntimeError("Google API libraries not installed. pip install google-api-python-client google-auth-oauthlib")

        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
        creds = flow.run_local_server(port=0)

        # Store token
        self.cred_store.set('gdrive', 'token_json', creds.to_json())
        self._creds = creds
        self._service = build('drive', 'v3', credentials=creds)
        logger.info("Google Drive authorized successfully")
        return True

    def connect(self):
        """Connect using stored credentials."""
        if not HAS_GDRIVE:
            raise RuntimeError("Google API libraries not installed")

        token_json = self.cred_store.get('gdrive', 'token_json')
        if not token_json:
            raise RuntimeError("Not authorized. Run authorize_interactive() first.")

        self._creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)

        # Refresh if expired
        if self._creds and self._creds.expired and self._creds.refresh_token:
            self._creds.refresh(Request())
            self.cred_store.set('gdrive', 'token_json', self._creds.to_json())

        self._service = build('drive', 'v3', credentials=self._creds)
        logger.info("Google Drive connected")
        return True

    def disconnect(self):
        """Revoke and remove credentials."""
        self.cred_store.delete('gdrive')
        self._service = None
        self._creds = None

    def test_connection(self):
        """Test if connection works."""
        try:
            if not self._service:
                self.connect()
            about = self._service.about().get(fields='user').execute()
            return {'ok': True, 'user': about['user']['emailAddress']}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def list_files(self, folder_id=None, query=None):
        """List files in Drive or a specific folder."""
        self._require_service()
        q_parts = ["trashed=false"]
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        if query:
            q_parts.append(query)
        q = " and ".join(q_parts)

        results = []
        page_token = None
        while True:
            resp = self._service.files().list(
                q=q,
                fields="nextPageToken, files(id, name, mimeType, size, md5Checksum, modifiedTime)",
                pageSize=100,
                pageToken=page_token,
            ).execute()
            results.extend(resp.get('files', []))
            page_token = resp.get('nextPageToken')
            if not page_token:
                break
        return results

    def upload_file(self, local_path, folder_id=None, remote_name=None):
        """Upload a file to Google Drive."""
        self._require_service()
        local_path = Path(local_path)
        name = remote_name or local_path.name

        file_metadata = {'name': name}
        if folder_id:
            file_metadata['parents'] = [folder_id]

        # Check if file exists (update vs create)
        existing = self._find_file(name, folder_id)

        media = MediaFileUpload(str(local_path), resumable=True)

        if existing:
            # Update existing
            result = self._service.files().update(
                fileId=existing['id'],
                media_body=media,
                fields='id, name, md5Checksum, modifiedTime, size',
            ).execute()
            logger.info(f"Updated: {name} ({result['id']})")
        else:
            # Create new
            result = self._service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, md5Checksum, modifiedTime, size',
            ).execute()
            logger.info(f"Uploaded: {name} ({result['id']})")

        return result

    def download_file(self, file_id, local_path):
        """Download a file from Google Drive."""
        self._require_service()
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp then rename (atomic)
        tmp = local_path.with_suffix('.gdrive_tmp')
        try:
            request = self._service.files().get_media(fileId=file_id)
            with open(tmp, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
            tmp.replace(local_path)
            logger.info(f"Downloaded: {local_path.name}")
            return True
        except Exception as e:
            if tmp.exists():
                tmp.unlink()
            raise e

    def get_file_meta(self, file_id):
        """Get file metadata including hash."""
        self._require_service()
        return self._service.files().get(
            fileId=file_id,
            fields='id, name, md5Checksum, modifiedTime, size, mimeType',
        ).execute()

    def create_folder(self, name, parent_id=None):
        """Create a folder in Drive."""
        self._require_service()
        meta = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder',
        }
        if parent_id:
            meta['parents'] = [parent_id]
        result = self._service.files().create(body=meta, fields='id, name').execute()
        return result

    def delete_file(self, file_id):
        """Move file to trash."""
        self._require_service()
        self._service.files().update(fileId=file_id, body={'trashed': True}).execute()

    def _find_file(self, name, folder_id=None):
        """Find a file by name in a folder."""
        q = f"name='{name}' and trashed=false"
        if folder_id:
            q += f" and '{folder_id}' in parents"
        resp = self._service.files().list(q=q, fields="files(id, name, md5Checksum)", pageSize=1).execute()
        files = resp.get('files', [])
        return files[0] if files else None

    def _require_service(self):
        if not self._service:
            self.connect()
