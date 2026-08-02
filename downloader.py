import os
import sys
import re
import argparse
import requests
import time
from dotenv import load_dotenv
from hls import HLSDownloader, DEFAULT_HEADERS
from gdrive import GoogleDriveUploader

# Load environment variables
load_dotenv()

DEFAULT_API_URL = "http://localhost:3000"
DEFAULT_DOWNLOAD_DIR = "downloads"

def print_banner():
    banner = """
==================================================
        IDLIX BATCH VIDEO DOWNLOADER CLI
==================================================
    """
    print(banner)

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

class IdlixDownloaderCLI:
    def __init__(self, api_url=DEFAULT_API_URL, download_dir=DEFAULT_DOWNLOAD_DIR, use_drive=False, workers=4):
        self.api_url = api_url.rstrip('/')
        self.download_dir = download_dir
        self.use_drive = use_drive
        self.workers = workers
        
        # Initialize Google Drive Uploader
        self.drive_uploader = GoogleDriveUploader()
        self.drive_active = False
        
        if self.use_drive:
            if self.drive_uploader.is_available():
                print("[Drive] Menginisialisasi otorisasi Google Drive...")
                if self.drive_uploader.authenticate():
                    self.drive_active = True
                    print("[Drive] ✅ Google Drive aktif. Unduhan akan diunggah secara otomatis.")
                else:
                    print("[Drive] ⚠️ Google Drive gagal diinisialisasi. Berkas hanya akan disimpan secara lokal.")
            else:
                print("[Drive] ⚠️ Pustaka Google Drive tidak terinstall. Berkas hanya akan disimpan secara lokal.")

    def search_tmdb(self, query, media_type, year=None):
        """Search media on TMDB to get the TMDB ID."""
        import os
        import requests
        api_key = os.getenv("TMDB_API_KEY", "b91a01595a49acfb313f677a0f8ee669")
        base_url = os.getenv("TMDB_BASE_URL", "https://api.themoviedb.org/3")
        
        tmdb_type = 'movie' if media_type == 'movie' else 'tv'
        url = f"{base_url}/search/{tmdb_type}"
        params = {
            "api_key": api_key,
            "query": query,
            "language": "en-US"
        }
        if year and media_type == 'movie':
            params["year"] = year
        elif year and media_type == 'series':
            params["first_air_date_year"] = year
            
        try:
            res = requests.get(url, params=params, timeout=10)
            res.raise_for_status()
            results = res.json().get("results", [])
            return results
        except Exception as e:
            print(f"[TMDB] ⚠️ Gagal mencari di TMDB: {str(e)}")
            return []

    def prompt_tmdb_id(self, title, media_type, year=None, auto_select=False):
        """Prompt user to select the TMDB ID, or auto-select the closest match if auto_select is True."""
        clean_title = title.replace(" (Batch Download)", "").replace(" (Batch Season)", "").strip()
        print(f"\n[TMDB] Mencocokkan '{clean_title}' dengan database TMDB...")
        results = self.search_tmdb(clean_title, media_type, year)
        
        if not results and " " in clean_title:
            short_query = " ".join(clean_title.split()[:3])
            results = self.search_tmdb(short_query, media_type, year)
            
        if results:
            if auto_select:
                best_match = results[0]
                name = best_match.get("title") if media_type == 'movie' else best_match.get("name")
                print(f"[TMDB] Otomatis memilih hasil terbaik: {name} (ID: {best_match.get('id')})")
                return best_match.get("id")

            print("\nSilakan pilih judul yang cocok dari TMDB:")
            for idx, item in enumerate(results[:5]):
                name = item.get("title") if media_type == 'movie' else item.get("name")
                date = item.get("release_date") if media_type == 'movie' else item.get("first_air_date")
                item_year = date.split("-")[0] if date else "-"
                print(f"  {idx + 1}. {name} ({item_year}) - ID: {item.get('id')}")
            print("  M. Masukkan TMDB ID secara manual")
            print("  S. Lewati integrasi TMDB / S3")
            
            pilihan = input("\nPilihan Anda (1-5/M/S): ").strip().upper()
            if pilihan == 'S':
                return None
            elif pilihan == 'M':
                val = input("Masukkan TMDB ID: ").strip()
                return int(val) if val.isdigit() else None
            else:
                try:
                    idx = int(pilihan) - 1
                    if 0 <= idx < len(results[:5]):
                        return results[idx].get("id")
                except ValueError:
                    pass
        else:
            print("⚠️ Tidak ditemukan hasil pencarian otomatis di TMDB.")
            pilihan = input("Masukkan TMDB ID secara manual atau tekan Enter untuk melewati: ").strip()
            if pilihan.isdigit():
                return int(pilihan)
                
        return None

    def handle_s3_upload_and_register(self, filepath, title, media_type, slug, tmdb_id, season=None, episode=None):
        """Upload downloaded file to S3 and register in imutflix-backend database."""
        import os
        import requests
        from s3 import upload_file_to_s3
        
        if not tmdb_id:
            print("[S3] ⚠️ Registrasi S3 dilewati karena TMDB ID tidak tersedia.")
            return False
            
        print("\n================== UNGGAH KE STORAGE S3 ==================")
        filename = os.path.basename(filepath)
        file_ext = os.path.splitext(filename)[1]
        
        if media_type == 'movie':
            s3_key = f"imutflix/movies/{tmdb_id}{file_ext}"
        else:
            s3_key = f"imutflix/series/{tmdb_id}/S{season:02d}E{episode:02d}{file_ext}"
            
        print(f"[S3] Memulai upload ke S3 dengan key: {s3_key}...")
        s3_url = upload_file_to_s3(filepath, s3_key)
        
        if not s3_url:
            print("[S3] ❌ Gagal mengunggah file ke S3.")
            return False
            
        print(f"[S3] ✅ Sukses mengunggah file. URL: {s3_url}")
        
        # Register to imutflix-backend DB
        backend_url = os.getenv("IMUTFLIX_BACKEND_URL", "http://localhost:3000")
        api_key = os.getenv("INTERNAL_API_KEY", "imutflix_internal_secret_api_key_2026")
        
        url = f"{backend_url.rstrip('/')}/api/internal/register-media"
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "tmdbId": tmdb_id,
            "mediaType": media_type,
            "title": title,
            "slug": slug,
            "season": season,
            "episode": episode,
            "s3Key": s3_key,
            "s3Url": s3_url,
            "filename": filename
        }
        
        print("[Database] Mendaftarkan file ke database backend...")
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=15)
            res.raise_for_status()
            result = res.json()
            if result.get("success"):
                print("[Database] ✅ Berhasil mendaftarkan media ke database backend!")
                return True
            else:
                print(f"[Database] ⚠️ Backend gagal mendaftarkan media: {result.get('message')}")
                return False
        except Exception as e:
            print(f"[Database] ❌ Gagal menghubungi backend database: {str(e)}")
            return False

    def sync_local_files_to_s3(self):
        """Scan local download directory and upload/register existing video files."""
        import os
        import re
        
        print("\n==================================================")
        print("     SINKRONISASI BERKAS LOKAL KE S3 & DATABASE    ")
        print("==================================================")
        
        if not os.path.exists(self.download_dir):
            print(f"⚠️ Direktori unduhan '{self.download_dir}' tidak ditemukan.")
            input("\nTekan Enter untuk kembali...")
            return
            
        # Get all video files
        valid_extensions = ('.mp4', '.mkv', '.ts')
        files = [f for f in os.listdir(self.download_dir) if f.lower().endswith(valid_extensions) and not f.startswith('temp_')]
        
        if not files:
            print("ℹ️ Tidak ditemukan berkas video (.mp4, .mkv, .ts) di folder unduhan.")
            input("\nTekan Enter untuk kembali...")
            return
            
        print(f"Ditemukan {len(files)} berkas video di folder unduhan:")
        for idx, filename in enumerate(files):
            print(f"  {idx + 1}. {filename}")
            
        print("\nMemulai proses pencocokan & unggah...")
        
        for idx, filename in enumerate(files):
            filepath = os.path.join(self.download_dir, filename)
            print(f"\n[{idx + 1}/{len(files)}] Memproses: {filename}")
            
            pilih = input(f"Unggah berkas ini? (y/n) [Default: y]: ").strip().lower()
            if pilih == 'n':
                print("Dilewati.")
                continue
                
            # Guess media type, season, and episode from filename
            media_type = 'movie'
            season = None
            episode = None
            
            # Match S01E01 patterns
            series_match = re.search(r"[sS](\d+)[eE](\d+)", filename)
            if series_match:
                media_type = 'series'
                season = int(series_match.group(1))
                episode = int(series_match.group(2))
                print(f"--> Terdeteksi sebagai Series (Season {season}, Episode {episode})")
            else:
                pilih_type = input("Tipe media? (1. Movie / 2. Series) [Default: 1]: ").strip()
                if pilih_type == '2':
                    media_type = 'series'
                    try:
                        season = int(input("Season ke: "))
                        episode = int(input("Episode ke: "))
                    except ValueError:
                        print("⚠️ Input harus angka. Dilewati.")
                        continue
            
            # Clean title for TMDB search
            clean_title = os.path.splitext(filename)[0]
            clean_title = re.sub(r" - S\d+E\d+.*", "", clean_title)
            clean_title = re.sub(r"\s*\(\d{4}\)\s*$", "", clean_title).strip()
            
            tmdb_id = self.prompt_tmdb_id(clean_title, media_type)
            if not tmdb_id:
                print("⚠️ TMDB ID dilewati. Berkas gagal didaftarkan.")
                continue
                
            # Generate slug from cleaned title
            slug = clean_title.lower().replace(" ", "-").replace(":", "")
            slug = re.sub(r"[^a-z0-9\-]", "", slug)
            
            # Upload & Register
            success = self.handle_s3_upload_and_register(
                filepath, clean_title, media_type, slug, tmdb_id, season, episode
            )
            
            if success:
                delete_local = input("Hapus berkas lokal ini setelah upload S3? (y/n) [Default: y]: ").strip().lower()
                if delete_local != 'n':
                    try:
                        os.remove(filepath)
                        print("[Lokal] Berkas lokal berhasil dihapus.")
                    except Exception as e:
                        print(f"[Lokal] Gagal menghapus berkas: {str(e)}")
                        
        input("\nProses sinkronisasi selesai. Tekan Enter untuk kembali...")

    def parse_idlix_url(self, url):
        """Parse IDLIX URL to extract media type and slug."""
        # Examples:
        # https://z2.idlixku.com/movie/per-aspera-ad-astra-2026
        # https://z2.idlixku.com/series/dream-to-you-2024
        # http://localhost:3000/movie/per-aspera-ad-astra-2026
        
        pattern = r"/(movie|series)/([a-zA-Z0-9\-]+)"
        match = re.search(pattern, url)
        if match:
            media_type = match.group(1)
            slug = match.group(2)
            return media_type, slug
        return None, None

    def search_media(self, query):
        """Search media on IDLIX API."""
        url = f"{self.api_url}/api/search"
        try:
            print(f"\n[Pencarian] Mencari '{query}' di IDLIX...")
            res = requests.get(url, params={"q": query}, timeout=15)
            res.raise_for_status()
            result = res.json()
            if result.get("success") and result.get("data"):
                return result["data"]
            return []
        except Exception as e:
            print(f"[Pencarian] ⚠️ Gagal menghubungi API server: {str(e)}")
            return None

    def get_series_detail(self, slug):
        """Fetch series details (seasons & episodes)."""
        url = f"{self.api_url}/api/series/{slug}"
        try:
            res = requests.get(url, timeout=15)
            res.raise_for_status()
            result = res.json()
            if result.get("success") and result.get("data"):
                return result["data"]
            return None
        except Exception as e:
            print(f"[API Error] Gagal mengambil detail series: {str(e)}")
            return None

    def extract_stream_url(self, media_type, slug, season=None, episode=None):
        """
        Request stream URL from IDLIX API.
        Handles the 15-second anti-scraping countdown.
        """
        if media_type == 'movie':
            url = f"{self.api_url}/api/movie/{slug}/stream"
        else:
            url = f"{self.api_url}/api/series/{slug}/season/{season}/episode/{episode}/stream"

        print(f"\n[1/2] Menghubungi API & Melewati Anti-Scraping IDLIX...")
        print("[Info] Proses ini memakan waktu ~15-20 detik karena proteksi countdown IDLIX. Harap tunggu...")
        
        # Simple text spinner/loading feedback
        start_time = time.time()
        
        try:
            res = requests.get(url, timeout=45) # 45 seconds timeout to allow for the countdown delay
            res.raise_for_status()
            result = res.json()
            
            elapsed = time.time() - start_time
            print(f"[Sukses] Link berhasil diekstrak dalam {elapsed:.1f} detik.")
            
            if result.get("success") and result.get("data"):
                return result["data"]
            else:
                print(f"[Gagal] API merespons sukses tetapi tidak ada data stream.")
                return None
        except Exception as e:
            print(f"[Gagal] Terjadi kesalahan saat ekstraksi: {str(e)}")
            return None

    def download_and_convert_subtitle(self, url, output_path):
        """Download WebVTT subtitle and convert it to SRT format."""
        try:
            res = requests.get(url, timeout=10)
            res.raise_for_status()
            vtt_content = res.text
            
            srt_content = self.convert_vtt_to_srt(vtt_content)
            
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(srt_content)
            print(f"[Subtitle] ✅ Subtitle berhasil diunduh & dikonversi: {os.path.basename(output_path)}")
            return True
        except Exception as e:
            print(f"[Subtitle] ⚠️ Gagal memproses subtitle: {str(e)}")
            return False

    def convert_vtt_to_srt(self, vtt_content):
        """Convert VTT format string to SRT format string."""
        lines = vtt_content.splitlines()
        srt_lines = []
        header_passed = False
        
        # Match standard VTT timestamp formats
        timestamp_re = re.compile(
            r'(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})'
        )
        
        counter = 1
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if not header_passed:
                if line.startswith('WEBVTT') or line.startswith('NOTE') or not line:
                    i += 1
                    continue
                else:
                    header_passed = True
                    
            match = timestamp_re.match(line)
            if match:
                sh_h = match.group(1) or "00"
                sh_m, sh_s, sh_ms = match.group(2), match.group(3), match.group(4)
                eh_h = match.group(5) or "00"
                eh_m, eh_s, eh_ms = match.group(6), match.group(7), match.group(8)
                
                srt_timestamp = f"{sh_h}:{sh_m}:{sh_s},{sh_ms} --> {eh_h}:{eh_m}:{eh_s},{eh_ms}"
                
                srt_lines.append(str(counter))
                srt_lines.append(srt_timestamp)
                counter += 1
                
                i += 1
                while i < len(lines):
                    text_line = lines[i].strip()
                    if not text_line:
                        srt_lines.append("")
                        break
                    elif timestamp_re.match(text_line):
                        srt_lines.append("")
                        break
                    else:
                        clean_line = re.sub(r'<[^>]+>', '', text_line)
                        srt_lines.append(clean_line)
                        i += 1
            i += 1
            
        return "\n".join(srt_lines)

    def get_ffmpeg_path(self):
        """Check if ffmpeg is available in current folder or system PATH."""
        # 1. Check in current directory
        local_ffmpeg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
        if os.path.exists(local_ffmpeg):
            return local_ffmpeg
        
        # 2. Check in system PATH
        import shutil
        sys_ffmpeg = shutil.which("ffmpeg")
        if sys_ffmpeg:
            return sys_ffmpeg
            
        return None

    def mux_to_mkv(self, video_path, audio_path, sub_path, output_path, ffmpeg_path):
        """Mux video, audio, and subtitle losslessly into a single .mkv container."""
        import subprocess
        print(f"[FFmpeg] Memproses remuxing lossless ke MKV...")
        try:
            cmd = [ffmpeg_path, "-y"]
            
            # Input video
            cmd.extend(["-i", video_path])
            
            # Input audio if separate
            if audio_path:
                cmd.extend(["-i", audio_path])
                
            # Input subtitle if present
            has_sub = sub_path and os.path.exists(sub_path)
            if has_sub:
                cmd.extend(["-i", sub_path])
                
            # Stream mapping
            cmd.extend(["-map", "0:v:0"])
            
            if audio_path:
                cmd.extend(["-map", "1:a:0"])
            else:
                # Map audio from source video if exists
                cmd.extend(["-map", "0:a:0?"])
                
            if has_sub:
                sub_index = "2" if audio_path else "1"
                cmd.extend(["-map", f"{sub_index}:s:0"])
                
            # Copy codecs (no re-encoding, 100% quality and resolution preserved!)
            cmd.extend([
                "-c:v", "copy",
                "-c:a", "copy"
            ])
            if has_sub:
                cmd.extend(["-c:s", "srt"])
                
            cmd.append(output_path)
            
            # Run FFmpeg with lower priority on Windows to prevent GUI lag/frame drops
            import sys
            creation_flags = 0
            if sys.platform == 'win32':
                # 0x00004000 is BELOW_NORMAL_PRIORITY_CLASS
                creation_flags = 0x00004000
            
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flags)
            if result.returncode == 0:
                print("[FFmpeg] ✅ Penggabungan lossless ke MKV selesai!")
                return True
            else:
                print(f"[FFmpeg] ⚠️ Gagal memproses remuxing. Code: {result.returncode}")
                err = result.stderr.decode('utf-8', errors='ignore')
                print(f"[FFmpeg Error] {err[-500:]}")
                return False
        except Exception as e:
            print(f"[FFmpeg] ⚠️ Kesalahan saat menjalankan FFmpeg: {str(e)}")
            return False

    def process_download(self, stream_data, output_filename, show_title, media_type='movie', slug=None, tmdb_id=None, season=None, episode=None, non_interactive=False):
        """Download video via HLS and optionally upload to Drive / S3 (non-interactive mode supported)."""
        import urllib.parse
        stream_url = stream_data.get("streamUrl")
        if not stream_url:
            print("[Download] ⚠️ Link streaming tidak ditemukan.")
            return False

        # Base clean filename
        base_name = os.path.splitext(output_filename)[0]
        safe_base = "".join([c for c in base_name if c.isalpha() or c.isdigit() or c in ' -_().']).strip()

        ffmpeg_path = self.get_ffmpeg_path()

        # Determine target output path (will be .mkv if ffmpeg is available, otherwise .mp4/.ts)
        if ffmpeg_path:
            filepath = os.path.join(self.download_dir, safe_base + ".mkv")
        else:
            filepath = os.path.join(self.download_dir, safe_base + ".mp4") # may be renamed to .ts later

        # Fetch playlist content to detect demuxed stream
        print(f"\n[Download] Membaca playlist master...")
        try:
            res = requests.get(stream_url, headers=DEFAULT_HEADERS, timeout=15)
            res.raise_for_status()
            playlist_content = res.text
        except Exception as e:
            print(f"[Download] ⚠️ Gagal mengunduh playlist master: {str(e)}")
            return False
            
        is_master = False
        video_url = None
        audio_url = None
        
        lines = playlist_content.splitlines()
        highest_bandwidth = 0
        
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF"):
                is_master = True
                match = re.search(r"BANDWIDTH=(\d+)", line)
                if match:
                    bandwidth = int(match.group(1))
                    if i + 1 < len(lines):
                        variant_url = lines[i+1].strip()
                        if not variant_url.startswith("#"):
                            full_variant_url = urllib.parse.urljoin(stream_url, variant_url)
                            if bandwidth > highest_bandwidth:
                                highest_bandwidth = bandwidth
                                video_url = full_variant_url
            elif line.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
                uri_match = re.search(r'URI="([^"]+)"', line)
                if uri_match:
                    audio_url = urllib.parse.urljoin(stream_url, uri_match.group(1))

        success = False
        audio_filepath = None
        sub_filepath = None
        
        # Temp paths for downloading raw components
        temp_video_path = os.path.join(self.download_dir, f"temp_video_{safe_base}.ts")
        temp_audio_path = os.path.join(self.download_dir, f"temp_audio_{safe_base}.ts")
        
        # Download subtitle first if available, so we can mux it directly!
        subtitles = stream_data.get("subtitles", [])
        if subtitles:
            sub_to_download = None
            # Find Indonesian subtitle first
            for sub in subtitles:
                lang = sub.get("lang", "").lower()
                label = sub.get("label", "").lower()
                if "id" in lang or "ind" in lang or "indonesia" in label:
                    sub_to_download = sub
                    break
            
            # Find English next
            if not sub_to_download:
                for sub in subtitles:
                    lang = sub.get("lang", "").lower()
                    label = sub.get("label", "").lower()
                    if "en" in lang or "eng" in lang or "english" in label:
                        sub_to_download = sub
                        break
            
            # Fallback to first available
            if not sub_to_download and subtitles:
                sub_to_download = subtitles[0]
                
            if sub_to_download:
                sub_url = sub_to_download.get("url")
                sub_label = sub_to_download.get("label", "Subtitle")
                sub_filepath = os.path.join(self.download_dir, f"{safe_base}.srt")
                
                print(f"\n[Subtitle] Menemukan subtitle: {sub_label}. Mengunduh...")
                self.download_and_convert_subtitle(sub_url, sub_filepath)

        # ── Case A: Separate Video/Audio Tracks ──
        if is_master and video_url and audio_url:
            print(f"[HLS] Aliran video dan audio terpisah terdeteksi.")
            
            print("\n================== UNDUH TREK VIDEO ==================")
            video_downloader = HLSDownloader(video_url, temp_video_path, max_workers=self.workers)
            video_success = video_downloader.start()
            temp_video_path = video_downloader.output_path
            
            print("\n================== UNDUH TREK AUDIO ==================")
            audio_downloader = HLSDownloader(audio_url, temp_audio_path, max_workers=self.workers)
            audio_success = audio_downloader.start()
            temp_audio_path = audio_downloader.output_path
            
            if video_success and audio_success:
                if ffmpeg_path:
                    # Mux video, audio, and subtitle into .mkv
                    success = self.mux_to_mkv(temp_video_path, temp_audio_path, sub_filepath, filepath, ffmpeg_path)
                    if success:
                        # Clean up temp video/audio and subtitle (since it is embedded!)
                        try:
                            os.remove(temp_video_path)
                            os.remove(temp_audio_path)
                            if sub_filepath and os.path.exists(sub_filepath):
                                os.remove(sub_filepath)
                                sub_filepath = None
                        except Exception:
                            pass
                    else:
                        # Fallback if ffmpeg fails: rename temp paths to final separate files
                        print("[Download] ⚠️ FFmpeg gagal memproses. Menyimpan berkas secara terpisah.")
                        video_ext = ".ts" if not video_downloader.is_fmp4 else ".mp4"
                        filepath = os.path.join(self.download_dir, safe_base + video_ext)
                        os.rename(temp_video_path, filepath)
                        
                        audio_ext = ".ts" if not video_downloader.is_fmp4 else ".mp4"
                        audio_filepath = os.path.join(self.download_dir, safe_base + "_audio" + audio_ext)
                        os.rename(temp_audio_path, audio_filepath)
                        success = True
                else:
                    # No FFmpeg: rename temp paths to final separate files
                    video_ext = ".ts" if not video_downloader.is_fmp4 else ".mp4"
                    filepath = os.path.join(self.download_dir, safe_base + video_ext)
                    os.rename(temp_video_path, filepath)
                    
                    audio_ext = ".ts" if not video_downloader.is_fmp4 else ".mp4"
                    audio_filepath = os.path.join(self.download_dir, safe_base + "_audio" + audio_ext)
                    os.rename(temp_audio_path, audio_filepath)
                    
                    print("\n[INFO PENTING] FFmpeg tidak terdeteksi di sistem Anda.")
                    print(f"--> Video disimpan tanpa suara di: {os.path.basename(filepath)}")
                    print(f"--> Audio disimpan di berkas terpisah: {os.path.basename(audio_filepath)}")
                    print("--> Untuk menggabungkannya otomatis, silakan letakkan 'ffmpeg.exe' di folder ini.")
                    success = True
            else:
                # Clean up temp downloads if one failed
                try:
                    if os.path.exists(temp_video_path): os.remove(temp_video_path)
                    if os.path.exists(temp_audio_path): os.remove(temp_audio_path)
                except Exception:
                    pass
                    
        # ── Case B: Single Track ──
        else:
            download_url = video_url if video_url else stream_url
            print(f"\n[HLS] Memulai download stream: {safe_base}")
            
            # Temporary path for raw download before remuxing
            temp_mux_path = os.path.join(self.download_dir, f"temp_mux_{safe_base}.ts")
            downloader = HLSDownloader(download_url, temp_mux_path, max_workers=self.workers)
            downloader_success = downloader.start()
            temp_mux_path = downloader.output_path
            
            if downloader_success:
                if ffmpeg_path:
                    # Remux and embed subtitle into .mkv
                    success = self.mux_to_mkv(temp_mux_path, None, sub_filepath, filepath, ffmpeg_path)
                    if success:
                        try:
                            os.remove(temp_mux_path)
                            if sub_filepath and os.path.exists(sub_filepath):
                                os.remove(sub_filepath)
                                sub_filepath = None
                        except Exception:
                            pass
                    else:
                        # Fallback if ffmpeg fails: keep raw downloader output
                        print("[Download] ⚠️ FFmpeg gagal memproses. Menyimpan berkas apa adanya.")
                        filepath = os.path.join(self.download_dir, safe_base + (".ts" if not downloader.is_fmp4 else ".mp4"))
                        os.rename(temp_mux_path, filepath)
                        success = True
                else:
                    # No FFmpeg: keep raw downloader output
                    filepath = os.path.join(self.download_dir, safe_base + (".ts" if not downloader.is_fmp4 else ".mp4"))
                    os.rename(temp_mux_path, filepath)
                    success = True

        if success:
            # Inform user
            print("\n" + "="*65)
            print("[INFO PEMUTAR VIDEO] Berkas berhasil diunduh!")
            if ffmpeg_path:
                print(f"--> File disimpan sebagai MKV Lossless: {os.path.basename(filepath)}")
                print("--> Subtitle telah ter-embed (softsub) langsung di dalam file MKV.")
                print("    Kualitas video/audio & resolusi 100% asli (tanpa kompresi).")
            else:
                print(f"--> File disimpan: {os.path.basename(filepath)}")
                if audio_filepath:
                    print(f"--> Audio disimpan terpisah: {os.path.basename(audio_filepath)}")
                if sub_filepath:
                    print(f"--> Subtitle .srt disimpan di sebelah file video.")
                print("\n[Rekomendasi] Gunakan pemutar VLC Player.")
                print("Untuk menggabungkan trek & subtitle secara otomatis ke .mkv tanpa")
                print("mengurangi kualitas, letakkan 'ffmpeg.exe' di folder aplikasi ini.")
            print("="*65 + "\n")

            if self.drive_active:
                # Upload Video
                upload_success = self.drive_uploader.upload_file(filepath, folder_name=show_title)
                
                # Upload Audio if separated
                if audio_filepath and os.path.exists(audio_filepath):
                    self.drive_uploader.upload_file(audio_filepath, folder_name=show_title)
                
                # Upload Subtitle if downloaded separately
                if sub_filepath and os.path.exists(sub_filepath):
                    self.drive_uploader.upload_file(sub_filepath, folder_name=show_title)
                
                if upload_success:
                    # Ask user if they want to delete local file
                    delete_local = input("\nHapus berkas lokal setelah sukses diunggah ke Drive? (y/n) [Default: y]: ").strip().lower()
                    if delete_local != 'n':
                        try:
                            os.remove(filepath)
                            print("[Lokal] Berkas video lokal dihapus.")
                            if audio_filepath and os.path.exists(audio_filepath):
                                os.remove(audio_filepath)
                                print("[Lokal] Berkas audio lokal dihapus.")
                            if sub_filepath and os.path.exists(sub_filepath):
                                os.remove(sub_filepath)
                                print("[Lokal] Berkas subtitle lokal dihapus.")
                        except Exception as e:
                            print(f"[Lokal] Gagal menghapus berkas lokal: {str(e)}")
            
            # S3 upload & Database registration
            if tmdb_id:
                register_success = self.handle_s3_upload_and_register(
                    filepath, show_title, media_type, slug, tmdb_id, season, episode
                )
                if register_success:
                    delete_local = 'y' if non_interactive else input("\nHapus berkas lokal setelah sukses diunggah ke S3? (y/n) [Default: y]: ").strip().lower()
                    if delete_local != 'n':
                        try:
                            if os.path.exists(filepath):
                                os.remove(filepath)
                                print("[Lokal] Berkas video lokal dihapus.")
                            if audio_filepath and os.path.exists(audio_filepath):
                                os.remove(audio_filepath)
                                print("[Lokal] Berkas audio lokal dihapus.")
                            if sub_filepath and os.path.exists(sub_filepath):
                                os.remove(sub_filepath)
                                print("[Lokal] Berkas subtitle lokal dihapus.")
                        except Exception as e:
                            print(f"[Lokal] Gagal menghapus berkas lokal: {str(e)}")
                            
        return success

    def run_movie_download(self, slug, title, year=None):
        """Orchestrate movie download."""
        display_title = f"{title} ({year})" if year else title
        print(f"\n==================================================")
        print(f"Mempersiapkan Unduhan Film: {display_title}")
        print(f"==================================================")
        
        # Prompt for TMDB ID first
        tmdb_id = self.prompt_tmdb_id(title, 'movie', year)
        
        stream_data = self.extract_stream_url('movie', slug)
        if not stream_data:
            input("\nTekan Enter untuk kembali ke menu utama...")
            return

        filename = f"{title} ({year}).mp4" if year else f"{title}.mp4"
        self.process_download(stream_data, filename, title, media_type='movie', slug=slug, tmdb_id=tmdb_id)
        input("\nProses selesai. Tekan Enter untuk kembali ke menu utama...")

    def run_series_download(self, slug, title):
        """Orchestrate series download with choices for batch or single episode."""
        # Prompt for TMDB ID once
        tmdb_id = self.prompt_tmdb_id(title, 'series')
        
        print("\n[Series] Mengambil informasi season dan episode...")
        detail = self.get_series_detail(slug)
        if not detail:
            print("⚠️ Gagal mengambil detail series.")
            input("\nTekan Enter untuk kembali...")
            return

        seasons = detail.get("seasons", [])
        if not seasons:
            print("⚠️ Tidak ada season yang ditemukan untuk series ini.")
            input("\nTekan Enter untuk kembali...")
            return

        # Populate episodes dynamically if empty
        for s in seasons:
            episodes = s.get("episodes", [])
            if not episodes:
                ep_count = s.get("episodeCount", 0)
                if ep_count == 0:
                    ep_count = 1  # Fallback to at least 1 episode
                populated_episodes = []
                for ep_num in range(1, ep_count + 1):
                    populated_episodes.append({
                        "episodeNumber": ep_num,
                        "title": f"Episode {ep_num}"
                    })
                s["episodes"] = populated_episodes

        while True:
            clear_screen()
            print_banner()
            print(f"Series Terpilih: {detail.get('title', title)}")
            print(f"Tahun: {detail.get('year', '-')}")
            print(f"Overview: {detail.get('overview', '-')[:120]}...\n")
            
            print("Daftar Season:")
            for idx, s in enumerate(seasons):
                print(f"  {idx + 1}. Season {s.get('seasonNumber', idx + 1)} ({len(s.get('episodes', []))} Episode)")
            print("\nPilihan Metode Unduh:")
            print("  A. Unduh SEMUA Episode (Batch Download)")
            print("  B. Unduh Season Tertentu")
            print("  C. Unduh Episode Spesifik")
            print("  K. Kembali ke Menu Utama")
            
            pilihan = input("\nMasukkan pilihan Anda (A/B/C/K): ").strip().upper()
            
            if pilihan == 'K':
                break
                
            elif pilihan == 'A':
                # Batch download all seasons & episodes
                all_episodes = []
                for s in seasons:
                    s_num = s.get("seasonNumber")
                    for ep in s.get("episodes", []):
                        all_episodes.append({
                            "season": s_num,
                            "episode": ep.get("episodeNumber"),
                            "title": ep.get("title", "")
                        })
                
                print(f"\n[Batch] Anda akan mengunduh TOTAL {len(all_episodes)} episode.")
                confirm = input("Apakah Anda yakin ingin melanjutkan? (y/n): ").strip().lower()
                if confirm != 'y':
                    continue
                
                for idx, ep in enumerate(all_episodes):
                    s_num = ep["season"]
                    ep_num = ep["episode"]
                    ep_title = ep["title"]
                    
                    print(f"\n[Batch {idx+1}/{len(all_episodes)}] Memproses S{s_num:02d}E{ep_num:02d}: {ep_title}")
                    
                    stream_data = self.extract_stream_url('series', slug, s_num, ep_num)
                    if not stream_data:
                        print(f"⚠️ Melewati episode S{s_num:02d}E{ep_num:02d} karena gagal mengambil stream link.")
                        continue
                    
                    filename = f"{title} - S{s_num:02d}E{ep_num:02d} - {ep_title}.mp4"
                    self.process_download(stream_data, filename, title, media_type='series', slug=slug, tmdb_id=tmdb_id, season=s_num, episode=ep_num)
                    print("-" * 50)
                
                input("\nBatch download selesai! Tekan Enter untuk kembali...")
                break
                
            elif pilihan == 'B':
                # Download specific season
                try:
                    s_idx = int(input(f"Pilih nomor Season (1-{len(seasons)}): ")) - 1
                    if s_idx < 0 or s_idx >= len(seasons):
                        print("Pilihan tidak valid.")
                        time.sleep(1.5)
                        continue
                except ValueError:
                    print("Input harus berupa angka.")
                    time.sleep(1.5)
                    continue
                    
                selected_season = seasons[s_idx]
                s_num = selected_season.get("seasonNumber")
                episodes = selected_season.get("episodes", [])
                
                print(f"\n[Batch Season {s_num}] Anda akan mengunduh {len(episodes)} episode.")
                confirm = input("Apakah Anda yakin? (y/n): ").strip().lower()
                if confirm != 'y':
                    continue
                    
                for idx, ep in enumerate(episodes):
                    ep_num = ep.get("episodeNumber")
                    ep_title = ep.get("title", "")
                    
                    print(f"\n[Season {s_num} - {idx+1}/{len(episodes)}] Memproses S{s_num:02d}E{ep_num:02d}: {ep_title}")
                    
                    stream_data = self.extract_stream_url('series', slug, s_num, ep_num)
                    if not stream_data:
                        print(f"⚠️ Melewati episode S{s_num:02d}E{ep_num:02d} karena gagal mengambil stream link.")
                        continue
                    
                    filename = f"{title} - S{s_num:02d}E{ep_num:02d} - {ep_title}.mp4"
                    self.process_download(stream_data, filename, title, media_type='series', slug=slug, tmdb_id=tmdb_id, season=s_num, episode=ep_num)
                    print("-" * 50)
                
                input(f"\nSeason {s_num} download selesai! Tekan Enter untuk kembali...")
                break
                
            elif pilihan == 'C':
                # Download specific episode
                try:
                    s_idx = int(input(f"Pilih nomor Season (1-{len(seasons)}): ")) - 1
                    if s_idx < 0 or s_idx >= len(seasons):
                        print("Pilihan tidak valid.")
                        time.sleep(1.5)
                        continue
                except ValueError:
                    print("Input harus berupa angka.")
                    time.sleep(1.5)
                    continue
                    
                selected_season = seasons[s_idx]
                s_num = selected_season.get("seasonNumber")
                episodes = selected_season.get("episodes", [])
                
                print(f"\nEpisode yang tersedia di Season {s_num}:")
                for idx, ep in enumerate(episodes):
                    print(f"  {idx + 1}. Episode {ep.get('episodeNumber')} - {ep.get('title', '')}")
                    
                try:
                    ep_idx = int(input(f"\nPilih nomor Episode (1-{len(episodes)}): ")) - 1
                    if ep_idx < 0 or ep_idx >= len(episodes):
                        print("Pilihan tidak valid.")
                        time.sleep(1.5)
                        continue
                except ValueError:
                    print("Input harus berupa angka.")
                    time.sleep(1.5)
                    continue
                    
                selected_ep = episodes[ep_idx]
                ep_num = selected_ep.get("episodeNumber")
                ep_title = selected_ep.get("title", "")
                
                print(f"\nMempersiapkan S{s_num:02d}E{ep_num:02d}: {ep_title}")
                stream_data = self.extract_stream_url('series', slug, s_num, ep_num)
                if not stream_data:
                    input("\nTekan Enter untuk kembali...")
                    continue
                    
                filename = f"{title} - S{s_num:02d}E{ep_num:02d} - {ep_title}.mp4"
                self.process_download(stream_data, filename, title, media_type='series', slug=slug, tmdb_id=tmdb_id, season=s_num, episode=ep_num)
                input("\nProses selesai. Tekan Enter untuk kembali...")
                break

    def check_movie_available(self, slug):
        """Check if a movie is already uploaded & registered in the backend."""
        import requests
        backend_url = os.getenv("IMUTFLIX_BACKEND_URL", "http://localhost:3001")
        url = f"{backend_url.rstrip('/')}/api/episodes/available"
        params = {"slug": slug, "type": "movie"}
        try:
            res = requests.get(url, params=params, timeout=5)
            if res.status_code == 200:
                return res.json().get("isAvailable", False)
        except Exception:
            pass
        return False

    def check_episode_available(self, slug, season, episode):
        """Check if a specific episode is already uploaded & registered in the backend."""
        import requests
        backend_url = os.getenv("IMUTFLIX_BACKEND_URL", "http://localhost:3001")
        url = f"{backend_url.rstrip('/')}/api/episodes/available"
        params = {"slug": slug, "type": "tvshows"}
        try:
            res = requests.get(url, params=params, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if data.get("isAvailable"):
                    seasons = data.get("seasons", [])
                    for s in seasons:
                        if s.get("seasonNumber") == season:
                            if episode in s.get("episodes", []):
                                return True
        except Exception:
            pass
        return False

    def run_batch_file_download(self):
        """Read URLs from a text file and download them automatically in non-interactive mode."""
        import os
        filename = "batch_urls.txt"
        
        print("\n==================================================")
        print("          BATCH DOWNLOAD DARI FILE URL            ")
        print("==================================================")
        print(f"Membaca daftar URL dari file: {filename}")
        
        if not os.path.exists(filename):
            with open(filename, "w") as f:
                f.write("# Masukkan URL IDLIX di sini (satu per baris)\n")
                f.write("# Contoh: https://z2.idlixku.com/movie/per-aspera-ad-astra-2026\n")
            print(f"⚠️ Berkas '{filename}' tidak ditemukan. Templat telah dibuat.")
            print("Silakan isi berkas tersebut dengan URL target terlebih dahulu.")
            input("\nTekan Enter untuk kembali...")
            return
            
        with open(filename, "r") as f:
            lines = f.readlines()
            
        items = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                if "|" in line:
                    parts = line.split("|")
                    url = parts[0].strip()
                    explicit_id = parts[1].strip()
                    explicit_id = int(explicit_id) if explicit_id.isdigit() else None
                    items.append((url, explicit_id))
                else:
                    items.append((line, None))
                
        if not items:
            print("ℹ️ Tidak ada URL yang valid ditemukan di dalam berkas.")
            input("\nTekan Enter untuk kembali...")
            return
            
        print(f"Ditemukan {len(items)} item dalam antrean download.")
        confirm = input("Apakah Anda yakin ingin memproses semua URL secara otomatis? (y/n) [Default: y]: ").strip().lower()
        if confirm == 'n':
            return
            
        for idx, item_data in enumerate(items):
            url, explicit_tmdb_id = item_data
            print(f"\n==================================================")
            print(f"PROSES [{idx + 1}/{len(items)}]: {url}")
            if explicit_tmdb_id:
                print(f"Menggunakan TMDB ID manual: {explicit_tmdb_id}")
            print("==================================================")
            
            media_type, slug = self.parse_idlix_url(url)
            if not media_type or not slug:
                print(f"⚠️ URL tidak valid dilewati: {url}")
                continue
                
            guessed_title = slug.replace("-", " ").title()
            
            if media_type == 'movie':
                # Check backend database availability first
                if self.check_movie_available(slug):
                    print(f"ℹ️ [Skip] Film '{guessed_title}' sudah terdaftar di backend database. Melewati unduhan.")
                    continue
                    
                tmdb_id = explicit_tmdb_id if explicit_tmdb_id else self.prompt_tmdb_id(guessed_title, 'movie', auto_select=True)
                stream_data = self.extract_stream_url('movie', slug)
                if not stream_data:
                    print(f"⚠️ Gagal mendapatkan link stream untuk: {guessed_title}")
                    continue
                    
                filename_out = f"{guessed_title}.mp4"
                self.process_download(
                    stream_data, filename_out, guessed_title, 
                    media_type='movie', slug=slug, tmdb_id=tmdb_id,
                    non_interactive=True
                )
            else:
                tmdb_id = explicit_tmdb_id if explicit_tmdb_id else self.prompt_tmdb_id(guessed_title, 'series', auto_select=True)
                print(f"\n[Series] Mengambil informasi season dan episode untuk: {guessed_title}...")
                detail = self.get_series_detail(slug)
                if not detail:
                    print(f"⚠️ Gagal mengambil detail series: {guessed_title}")
                    continue
                    
                seasons = detail.get("seasons", [])
                if not seasons:
                    print(f"⚠️ Tidak ada season yang ditemukan.")
                    continue
                    
                for s in seasons:
                    episodes = s.get("episodes", [])
                    if not episodes:
                        ep_count = s.get("episodeCount", 0) or 1
                        populated_episodes = [{"episodeNumber": ep_num, "title": f"Episode {ep_num}"} for ep_num in range(1, ep_count + 1)]
                        s["episodes"] = populated_episodes
                
                all_episodes = []
                for s in seasons:
                    s_num = s.get("seasonNumber")
                    for ep in s.get("episodes", []):
                        all_episodes.append({
                            "season": s_num,
                            "episode": ep.get("episodeNumber"),
                            "title": ep.get("title", "")
                        })
                        
                print(f"[Series] Ditemukan total {len(all_episodes)} episode. Memulai batch download...")
                for ep_idx, ep in enumerate(all_episodes):
                    s_num = ep["season"]
                    ep_num = ep["episode"]
                    ep_title = ep["title"]
                    
                    # Check backend database availability first for this episode
                    if self.check_episode_available(slug, s_num, ep_num):
                        print(f"ℹ️ [Skip] Episode S{s_num:02d}E{ep_num:02d} - {ep_title} sudah terdaftar di backend database. Melewati.")
                        continue
                        
                    print(f"\n[{ep_idx + 1}/{len(all_episodes)}] Memproses S{s_num:02d}E{ep_num:02d}: {ep_title}")
                    stream_data = self.extract_stream_url('series', slug, s_num, ep_num)
                    if not stream_data:
                        print(f"⚠️ Melewati S{s_num:02d}E{ep_num:02d} karena gagal ambil stream.")
                        continue
                        
                    filename_out = f"{guessed_title} - S{s_num:02d}E{ep_num:02d} - {ep_title}.mp4"
                    self.process_download(
                        stream_data, filename_out, guessed_title,
                        media_type='series', slug=slug, tmdb_id=tmdb_id,
                        season=s_num, episode=ep_num,
                        non_interactive=True
                    )
                    
        print("\n==================================================")
        print("          BATCH FILE PROCESS COMPLETED!           ")
        print("==================================================")
        input("\nTekan Enter untuk kembali ke menu utama...")

    def main_loop(self):
        """Main CLI event loop."""
        # Ensure download directory exists
        os.makedirs(self.download_dir, exist_ok=True)
        
        while True:
            clear_screen()
            print_banner()
            print(f"Konfigurasi Aktif:")
            print(f"  - API URL        : {self.api_url}")
            print(f"  - Folder Unduhan : {os.path.abspath(self.download_dir)}")
            print(f"  - Google Drive   : {'AKTIF' if self.drive_active else 'NON-AKTIF / TIDAK TERSEDIA'}")
            print("==================================================")
            
            print("\nMenu Utama:")
            print("  1. Cari Film/Series berdasarkan judul")
            print("  2. Masukkan URL IDLIX langsung")
            print("  3. Hubungkan ke Google Drive")
            print("  4. Sinkronisasi Berkas Lokal ke S3 & Database")
            print("  5. Auto Download Banyak URL dari File (batch_urls.txt)")
            print("  6. Keluar")
            
            pilihan = input("\nPilih menu (1-6): ").strip()
            
            if pilihan == '6':
                print("\nTerima kasih telah menggunakan IDLIX Downloader. Sampai jumpa!")
                break
                
            elif pilihan == '5':
                self.run_batch_file_download()
                
            elif pilihan == '4':
                self.sync_local_files_to_s3()
                
            elif pilihan == '3':
                if self.drive_active:
                    print("\n[Drive] Google Drive sudah terhubung dan aktif.")
                    input("Tekan Enter untuk kembali...")
                else:
                    self.drive_uploader = GoogleDriveUploader()
                    if self.drive_uploader.authenticate():
                        self.drive_active = True
                        print("[Drive] ✅ Berhasil terhubung ke Google Drive!")
                    else:
                        print("[Drive] ❌ Gagal menghubungkan ke Google Drive. Cek kredensial Anda.")
                    input("\nTekan Enter untuk kembali...")
                    
            elif pilihan == '2':
                url = input("\nMasukkan URL Film/Series IDLIX: ").strip()
                media_type, slug = self.parse_idlix_url(url)
                
                if not media_type or not slug:
                    print("⚠️ URL tidak valid. Pastikan formatnya benar (misal: .../movie/judul atau .../series/judul)")
                    input("\nTekan Enter untuk kembali...")
                    continue
                
                # Guess clean title from slug
                guessed_title = slug.replace("-", " ").title()
                
                if media_type == 'movie':
                    self.run_movie_download(slug, guessed_title)
                else:
                    self.run_series_download(slug, guessed_title)
                    
            elif pilihan == '1':
                query = input("\nMasukkan judul film atau series: ").strip()
                if len(query) < 2:
                    print("⚠️ Judul pencarian minimal 2 karakter.")
                    input("\nTekan Enter untuk kembali...")
                    continue
                
                results = self.search_media(query)
                if results is None:
                    input("\nTekan Enter untuk kembali...")
                    continue
                    
                if not results:
                    print("⚠️ Tidak ada film atau series yang cocok dengan judul tersebut.")
                    input("\nTekan Enter untuk kembali...")
                    continue
                
                while True:
                    clear_screen()
                    print_banner()
                    print(f"Hasil Pencarian untuk: '{query}'")
                    print("==================================================")
                    for idx, item in enumerate(results):
                        item_type = "Movie" if item.get("type") == "movie" else "Series"
                        year = item.get("year", "-")
                        print(f"  {idx + 1}. {item.get('title')} ({year}) [{item_type}]")
                    print("  K. Kembali ke Menu Utama")
                    
                    pilihan_hasil = input("\nPilih nomor hasil pencarian (1-N) atau K: ").strip()
                    
                    if pilihan_hasil.upper() == 'K':
                        break
                        
                    try:
                        res_idx = int(pilihan_hasil) - 1
                        if 0 <= res_idx < len(results):
                            selected = results[res_idx]
                            slug = selected.get("slug")
                            title = selected.get("title")
                            media_type = selected.get("type")
                            year = selected.get("year")
                            
                            if media_type == 'movie':
                                self.run_movie_download(slug, title, year)
                            else:
                                self.run_series_download(slug, title)
                            break
                        else:
                            print("Pilihan di luar jangkauan.")
                            time.sleep(1)
                    except ValueError:
                        print("Input tidak valid.")
                        time.sleep(1)
            else:
                print("Menu tidak valid.")
                time.sleep(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IDLIX Video Downloader CLI")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="URL server IDLIX-API (default: http://localhost:3000)")
    parser.add_argument("--out-dir", default=DEFAULT_DOWNLOAD_DIR, help="Direktori penyimpanan video (default: downloads)")
    parser.add_argument("--drive", action="store_true", help="Aktifkan sinkronisasi otomatis ke Google Drive")
    parser.add_argument("--workers", type=int, default=4, help="Jumlah thread worker untuk download paralel (default: 4)")
    
    args = parser.parse_args()
    
    cli = IdlixDownloaderCLI(
        api_url=args.api_url,
        download_dir=args.out_dir,
        use_drive=args.drive,
        workers=args.workers
    )
    
    try:
        cli.main_loop()
    except KeyboardInterrupt:
        print("\n\nKeluar dari program. Terima kasih!")
        sys.exit(0)
