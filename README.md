# IDLIX Batch Video Downloader CLI

Utilitas baris perintah (CLI) berbasis Python untuk mengunduh film dan series dari platform IDLIX secara batch (banyak episode sekaligus) maupun satu per satu, serta mengunggahnya secara langsung ke Google Drive.

## 🚀 Fitur Utama

- **Pencarian Interaktif:** Cari film atau series cukup dengan mengetikkan judulnya langsung dari terminal.
- **Batch Download Series:** Unduh seluruh episode dari suatu series secara otomatis, per season, atau pilih episode spesifik.
- **Dukungan Direct URL:** Masukkan link URL film/series IDLIX langsung dari browser untuk mulai mengunduh.
- **Pengunduh HLS Mandiri:** Mengunduh dan menggabungkan stream HLS (`.m3u8` / `.ts`) secara paralel (multi-threaded) serta mendukung dekripsi AES-128 secara otomatis **tanpa memerlukan instalasi FFmpeg** di sistem Anda.
- **Integrasi Google Drive (Opsional):** Unggah otomatis video yang telah diunduh ke Google Drive Anda (lengkap dengan pembuatan folder teratur) dan opsi hapus berkas lokal setelah unggahan selesai untuk menghemat ruang penyimpanan.

---

## 🛠️ Prasyarat & Instalasi

### 1. Jalankan IDLIX API Server
Aplikasi CLI ini berkomunikasi dengan `idlix-api` server untuk memproses pencarian dan ekstraksi tautan video. Pastikan server API sudah menyala di sistem Anda.

Jika Anda menggunakan Docker (direkomendasikan):
```bash
cd ../idlix-api
docker compose up -d
```
API server akan berjalan di `http://localhost:3000`.

### 2. Instal Dependensi Python
Buka terminal CMD/PowerShell di folder `idlix-downloader` ini dan jalankan perintah:
```bash
pip install -r requirements.txt
```

---

## 📂 Cara Penggunaan

Jalankan skrip utama menggunakan Python di terminal Anda:
```bash
python downloader.py
```

### Argumen Command Line (Opsional)
Anda dapat menyesuaikan konfigurasi default menggunakan opsi berikut:
- `--api-url`: Alamat server `idlix-api` (Default: `http://localhost:3000`)
- `--out-dir`: Lokasi folder untuk menyimpan video hasil unduhan (Default: `downloads`)
- `--drive`   : Aktifkan fitur integrasi Google Drive secara langsung sejak startup

Contoh:
```bash
python downloader.py --out-dir "D:\Film Unduhan" --drive
```

---

## ☁️ Konfigurasi Google Drive (Opsional)

Untuk mengaktifkan pengunggahan otomatis ke Google Drive Anda:

1. Kunjungi [Google Cloud Console](https://console.cloud.google.com/).
2. Buat proyek baru (jika belum ada).
3. Cari dan aktifkan **Google Drive API** untuk proyek tersebut.
4. Masuk ke halaman **OAuth consent screen**, pilih tipe **External**, dan isi informasi dasar. Pastikan tambahkan email Anda ke daftar **Test Users** jika proyek masih dalam status Testing.
5. Kunjungi halaman **Credentials**, klik **Create Credentials** -> **OAuth Client ID**.
6. Pilih Application type: **Desktop Application**, lalu klik Create.
7. Unduh file JSON kredensial tersebut, ubah namanya menjadi `credentials.json`, dan letakkan di dalam folder `idlix-downloader/` ini.
8. Jalankan program dengan bendera `--drive` atau pilih menu **Hubungkan ke Google Drive** di menu utama program. Jendela browser akan terbuka secara otomatis untuk proses login Google pertama kali. Token otorisasi Anda akan disimpan di file `token.json` agar tidak perlu login ulang di kemudian hari.

---

## 💡 Informasi Tambahan & Catatan Penting

- **Proteksi Countdown IDLIX:** IDLIX memiliki proteksi anti-scraping internal berupa penundaan waktu (countdown gate) selama 15 detik untuk setiap tautan video. Oleh karena itu, ketika program mengambil tautan video (langkah `[1/2]`), proses akan memakan waktu sekitar **15-20 detik per video/episode**. Ini adalah perilaku normal dan program tidak membeku/hang.
- **Ekstensi Berkas:** HLS stream terdiri dari potongan berkas `.ts` (MPEG transport stream) yang digabung menjadi satu berkas berformat MPEG-4 TS. Program secara default akan menyimpan berkas gabungan ini dengan ekstensi `.mp4` agar kompatibel dengan pemutar video bawaan OS Anda. Jika video tidak dapat diputar, gunakan pemutar serbaguna seperti **VLC Media Player**.
