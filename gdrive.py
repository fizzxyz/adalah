import os

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GDRIVE_AVAILABLE = True
except ImportError:
    GDRIVE_AVAILABLE = False

# Scopes required for Google Drive access (read/write access to files created by this app)
SCOPES = ['https://www.googleapis.com/auth/drive.file']

class GoogleDriveUploader:
    def __init__(self, credentials_path="credentials.json", token_path="token.json"):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        
    def is_available(self):
        """Check if google drive packages are installed."""
        return GDRIVE_AVAILABLE

    def authenticate(self):
        """Authenticate user and initialize the drive service."""
        if not GDRIVE_AVAILABLE:
            print("[Google Drive] Dependensi google-api-client tidak terinstall. Silakan jalankan `pip install -r requirements.txt`")
            return False

        creds = None
        # token.json stores user credentials after authentication is successful
        if os.path.exists(self.token_path):
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except Exception:
                pass

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None
            
            if not creds:
                if not os.path.exists(self.credentials_path):
                    print(f"\n[Drive Warning] File '{self.credentials_path}' tidak ditemukan!")
                    print("Untuk mengaktifkan fitur upload Google Drive, silakan:")
                    print("1. Kunjungi Google Cloud Console (https://console.cloud.google.com/)")
                    print("2. Buat project baru dan aktifkan Google Drive API")
                    print("3. Buat kredensial OAuth Client ID (pilih Desktop Application)")
                    print(f"4. Unduh file JSON kredensial tersebut dan simpan sebagai '{self.credentials_path}' di folder ini.")
                    return False
                
                print("\n[Drive Authentication] Membuka browser untuk otorisasi Google Drive...")
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                    creds = flow.run_local_server(port=0)
                    # Save the credentials for the next run
                    with open(self.token_path, 'w') as token:
                        token.write(creds.to_json())
                except Exception as e:
                    print(f"[Drive Error] Gagal melakukan otorisasi: {str(e)}")
                    return False

        try:
            self.service = build('drive', 'v3', credentials=creds)
            return True
        except Exception as e:
            print(f"[Drive Error] Gagal menginisialisasi Google Drive service: {str(e)}")
            return False

    def get_or_create_folder(self, folder_name, parent_id=None):
        """Get existing folder ID or create a new one by name."""
        if not self.service:
            return None

        # Search query
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
            
        try:
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            items = results.get('files', [])
            
            if items:
                return items[0]['id']
            
            # If not exists, create new folder
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            if parent_id:
                file_metadata['parents'] = [parent_id]
                
            folder = self.service.files().create(body=file_metadata, fields='id').execute()
            return folder.get('id')
        except Exception as e:
            print(f"[Drive Error] Gagal mengelola folder '{folder_name}': {str(e)}")
            return None

    def upload_file(self, filepath, folder_name=None, parent_folder_id=None):
        """Upload a file to Google Drive (optionally inside a folder)."""
        if not self.service:
            print("[Drive] Layanan Google Drive belum terotorisasi.")
            return False

        if not os.path.exists(filepath):
            print(f"[Drive] File tidak ditemukan: {filepath}")
            return False

        filename = os.path.basename(filepath)
        print(f"[Drive] Mempersiapkan upload untuk: {filename}")

        # Resolve destination folder
        parents = []
        if folder_name:
            folder_id = self.get_or_create_folder(folder_name, parent_id=parent_folder_id)
            if folder_id:
                parents = [folder_id]
        elif parent_folder_id:
            parents = [parent_folder_id]

        file_metadata = {'name': filename}
        if parents:
            file_metadata['parents'] = parents

        # Guess mimetype (most output will be video/mp4 or video/MP2T)
        mimetype = 'video/mp4'
        if filepath.endswith('.ts'):
            mimetype = 'video/MP2T'

        try:
            from tqdm import tqdm
            
            media = MediaFileUpload(filepath, mimetype=mimetype, chunksize=5*1024*1024, resumable=True)
            request = self.service.files().create(body=file_metadata, media_body=media)
            
            response = None
            file_size = os.path.getsize(filepath)
            
            with tqdm(total=file_size, unit='B', unit_scale=True, desc=f"Mengunggah '{filename}'", leave=True) as pbar:
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        pbar.n = int(status.resumable_progress)
                        pbar.refresh()
            
            file_id = response.get('id')
            print(f"[Drive] ✅ Berhasil mengunggah '{filename}' ke Google Drive! File ID: {file_id}")
            return file_id
        except Exception as e:
            print(f"\n[Drive Error] Gagal mengunggah berkas: {str(e)}")
            return None
