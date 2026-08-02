import os
import boto3
from boto3.s3.transfer import TransferConfig
from tqdm import tqdm
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()

class ProgressPercentage(object):
    def __init__(self, filename, filesize):
        self._filename = filename
        self._size = filesize
        self._seen_so_far = 0
        self._pbar = tqdm(
            total=self._size,
            unit='B',
            unit_scale=True,
            desc=f"[S3] Mengunggah '{os.path.basename(filename)}'",
            leave=True
        )

    def __call__(self, bytes_amount):
        self._seen_so_far += bytes_amount
        self._pbar.update(bytes_amount)
        if self._seen_so_far >= self._size:
            self._pbar.close()

def get_s3_client():
    endpoint = os.getenv("S3_ENDPOINT")
    access_key = os.getenv("S3_ACCESS_KEY_ID")
    secret_key = os.getenv("S3_SECRET_ACCESS_KEY")
    
    if not all([endpoint, access_key, secret_key]):
        return None
        
    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name='auto',
        config=boto3.session.Config(
            signature_version='s3v4',
            s3={
                'addressing_style': 'path',
                'payload_signing_enabled': False # Match dashboard unsigned payload
            }
        )
    )

def upload_file_to_s3(local_path, s3_key):
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        print("[S3] ⚠️ S3_BUCKET tidak terkonfigurasi di .env")
        return False
        
    client = get_s3_client()
    if not client:
        print("[S3] ⚠️ Kredensial S3 tidak lengkap di .env")
        return False
        
    if not os.path.exists(local_path):
        print(f"[S3] ⚠️ File lokal tidak ditemukan: {local_path}")
        return False
        
    filesize = os.path.getsize(local_path)
    
    # Configure multipart upload threshold (8MB chunk size)
    config = TransferConfig(
        multipart_threshold=1024 * 1024 * 8,
        max_concurrency=4,
        multipart_chunksize=1024 * 1024 * 8,
        use_threads=True
    )
    
    try:
        progress = ProgressPercentage(local_path, filesize)
        
        # Extra args
        extra_args = {}
        if local_path.lower().endswith('.mp4'):
            extra_args['ContentType'] = 'video/mp4'
        elif local_path.lower().endswith('.mkv'):
            extra_args['ContentType'] = 'video/x-matroska'
        elif local_path.lower().endswith('.ts'):
            extra_args['ContentType'] = 'video/mp2t'
            
        client.upload_file(
            Filename=local_path,
            Bucket=bucket,
            Key=s3_key,
            Config=config,
            ExtraArgs=extra_args,
            Callback=progress
        )
        
        # Construct output URL
        endpoint_clean = os.getenv("S3_ENDPOINT").rstrip('/')
        s3_url = f"{endpoint_clean}/{bucket}/{s3_key}"
        return s3_url
    except Exception as e:
        print(f"\n[S3 Error] Gagal mengunggah berkas ke S3: {str(e)}")
        return False
