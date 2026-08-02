import os
import re
import urllib.parse
import concurrent.futures
import shutil
import tempfile
import threading
import time
import requests
from tqdm import tqdm
from Crypto.Cipher import AES

# Standard headers to bypass basic blockages on CDNs
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Origin': 'https://z2.idlixku.com',
    'Referer': 'https://z2.idlixku.com/',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
}

class HLSDownloader:
    def __init__(self, m3u8_url, output_path, headers=None, max_workers=4):
        self.m3u8_url = m3u8_url
        self.output_path = output_path
        self.headers = headers or DEFAULT_HEADERS
        self.max_workers = max_workers
        self._local = threading.local()
        self._lock = threading.Lock()
        self.is_fmp4 = False
        
        # Cache for encryption keys to avoid refetching the same key
        self.key_cache = {}

    @property
    def session(self):
        if not hasattr(self._local, 'session'):
            self._local.session = requests.Session()
            self._local.session.headers.update(self.headers)
        return self._local.session

    def fetch_playlist_content(self, url):
        """Fetch playlist content from URL."""
        res = self.session.get(url, timeout=15)
        res.raise_for_status()
        return res.text

    def parse_m3u8(self, url, content):
        """Parse m3u8 playlist. Handles master playlist redirects."""
        lines = content.splitlines()
        
        # Check if master playlist
        is_master = False
        highest_bandwidth_url = None
        highest_bandwidth = 0

        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF"):
                is_master = True
                # Parse bandwidth
                match = re.search(r"BANDWIDTH=(\d+)", line)
                if match:
                    bandwidth = int(match.group(1))
                    # Get the URL (next line)
                    if i + 1 < len(lines):
                        variant_url = lines[i+1].strip()
                        if not variant_url.startswith("#"):
                            full_variant_url = urllib.parse.urljoin(url, variant_url)
                            if bandwidth > highest_bandwidth:
                                highest_bandwidth = bandwidth
                                highest_bandwidth_url = full_variant_url

        if is_master and highest_bandwidth_url:
            print(f"[HLS] Master playlist terdeteksi. Memilih kualitas tertinggi (Bandwidth: {highest_bandwidth})...")
            content = self.fetch_playlist_content(highest_bandwidth_url)
            return self.parse_m3u8(highest_bandwidth_url, content)

        # Parse media playlist segments
        segments = []
        media_seq = 0
        active_key_info = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("#EXT-X-MEDIA-SEQUENCE"):
                match = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", line)
                if match:
                    media_seq = int(match.group(1))
            
            elif line.startswith("#EXT-X-MAP"):
                # Format: #EXT-X-MAP:URI="intro.html"
                uri_match = re.search(r'URI="([^"]+)"', line)
                if uri_match:
                    full_map_url = urllib.parse.urljoin(url, uri_match.group(1))
                    segments.append({
                        "url": full_map_url,
                        "seq": -1,
                        "key_info": active_key_info
                    })

            elif line.startswith("#EXT-X-KEY"):
                # Format: #EXT-X-KEY:METHOD=AES-128,URI="https://key-url",IV=0x0102...
                method_match = re.search(r"METHOD=([^,\s]+)", line)
                uri_match = re.search(r'URI="([^"]+)"', line)
                iv_match = re.search(r"IV=0[xX]([a-fA-F0-9]+)", line)

                method = method_match.group(1) if method_match else None
                key_uri = uri_match.group(1) if uri_match else None
                iv = iv_match.group(1) if iv_match else None

                if method == "AES-128" and key_uri:
                    # Resolve key URL relative to current playlist URL
                    full_key_url = urllib.parse.urljoin(url, key_uri)
                    active_key_info = {
                        "method": method,
                        "key_url": full_key_url,
                        "iv_hex": iv
                    }
                else:
                    active_key_info = None

            elif not line.startswith("#"):
                # This is a segment URL
                segment_url = urllib.parse.urljoin(url, line)
                segments.append({
                    "url": segment_url,
                    "seq": media_seq,
                    "key_info": active_key_info
                })
                media_seq += 1

        return segments

    def get_encryption_key(self, key_url):
        """Fetch encryption key from key_url. Caches it."""
        with self._lock:
            if key_url in self.key_cache:
                return self.key_cache[key_url]

        # Use same headers for key fetching, as CDNs may restrict it
        res = self.session.get(key_url, timeout=10)
        res.raise_for_status()
        key_bytes = res.content
        
        if len(key_bytes) != 16:
            raise ValueError(f"Panjang key tidak valid (harus 16 bytes, didapat {len(key_bytes)} bytes)")
            
        with self._lock:
            self.key_cache[key_url] = key_bytes
        return key_bytes

    def download_segment(self, segment, temp_dir):
        """Download and optionally decrypt a single segment, saving it to temp folder."""
        seq = segment["seq"]
        url = segment["url"]
        key_info = segment["key_info"]
        
        if seq == -1:
            temp_filepath = os.path.join(temp_dir, "segment_init.ts")
        else:
            temp_filepath = os.path.join(temp_dir, f"segment_{seq:06d}.ts")

        # Skip if already downloaded (for robustness)
        if os.path.exists(temp_filepath):
            return True

        retries = 3
        for attempt in range(retries):
            try:
                res = self.session.get(url, timeout=15)
                res.raise_for_status()
                data = res.content

                # Decrypt if AES-128
                if key_info and key_info["method"] == "AES-128":
                    key = self.get_encryption_key(key_info["key_url"])
                    
                    # Determine IV
                    if key_info["iv_hex"]:
                        iv = bytes.fromhex(key_info["iv_hex"])
                    else:
                        # If IV is not specified, use sequence number
                        iv = seq.to_bytes(16, byteorder='big')

                    # Perform decryption
                    cipher = AES.new(key, AES.MODE_CBC, iv)
                    data = cipher.decrypt(data)

                # Save decrypted/raw segment
                with open(temp_filepath, "wb") as f:
                    f.write(data)
                return True

            except Exception as e:
                if attempt == retries - 1:
                    print(f"\n[Gagal] Gagal mengunduh segmen {seq} setelah {retries} percobaan: {str(e)}")
                    raise e
                continue

    def start(self):
        """Orchestrate the download of the HLS playlist."""
        print(f"[HLS] Membaca playlist M3U8...")
        playlist_content = self.fetch_playlist_content(self.m3u8_url)
        segments = self.parse_m3u8(self.m3u8_url, playlist_content)
        
        # Adjust file extension if standard MPEG-TS HLS (not fMP4)
        if not self.is_fmp4 and self.output_path.lower().endswith(".mp4"):
            self.output_path = self.output_path[:-4] + ".ts"
            
        total_segments = len(segments)
        if total_segments == 0:
            print("[HLS] Playlist kosong atau tidak bisa diurai.")
            return False

        print(f"[HLS] Ditemukan {total_segments} segmen video.")
        
        # Create temp folder
        temp_dir = tempfile.mkdtemp(prefix="idlix_downloader_")
        
        try:
            # Parallel downloads
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Use tqdm progress bar
                futures = {executor.submit(self.download_segment, seg, temp_dir): seg for seg in segments}
                
                with tqdm(total=total_segments, desc="Mengunduh Video", unit="segmen", leave=True) as pbar:
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result()
                            pbar.update(1)
                        except Exception as e:
                            # Cancel other downloads if one fails critically
                            print(f"\n[HLS] Kegagalan kritis terjadi: {str(e)}. Membatalkan unduhan...")
                            raise e

            # Stitches segments together
            print(f"[HLS] Menyatukan {total_segments} segmen ke berkas akhir...")
            
            # Ensure parent directory of output_path exists
            output_dir = os.path.dirname(self.output_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
                
            with open(self.output_path, "wb") as outfile:
                for i in range(total_segments):
                    seq = segments[i]["seq"]
                    if seq == -1:
                        segment_file = os.path.join(temp_dir, "segment_init.ts")
                    else:
                        segment_file = os.path.join(temp_dir, f"segment_{seq:06d}.ts")
                    if os.path.exists(segment_file):
                        with open(segment_file, "rb") as infile:
                            shutil.copyfileobj(infile, outfile)
                    
                    # Yield CPU/Disk scheduler time every 30 segments to prevent UI hang/not responding
                    if i % 30 == 0:
                        time.sleep(0.005)
            
            print(f"[HLS] ✅ Pengunduhan selesai! Tersimpan di: {self.output_path}")
            return True

        finally:
            # Clean up temp files and directory
            shutil.rmtree(temp_dir, ignore_errors=True)
