# ServiceHub – Internal Help Desk System

Aplikasi help desk internal berbasis Flask dan SQLite untuk pengelolaan tiket, monitoring SLA, dashboard operasional, master data, audit trail, serta reporting agregat.

## Menjalankan aplikasi di Windows (cara termudah)

Klik dua kali `start.bat`. Pada pemakaian pertama, launcher akan otomatis:

- membuat virtual environment lokal `.venv` agar tidak tercampur dengan Anaconda/Python lain;
- memasang versi dependensi yang tepat dari `requirements.txt`;
- membuat database SQLite dan akun demo jika database masih kosong; dan
- menjalankan aplikasi di `http://127.0.0.1:8000`.

Koneksi database dan folder upload pada mode ini selalu berada di folder `instance`
di dalam project, sehingga project dapat dipindahkan ke folder/laptop lain. Python
3.10 atau lebih baru harus sudah terpasang. Koneksi internet hanya diperlukan saat
setup pertama untuk mengunduh dependensi.

Jangan menyalin folder `.venv` ke laptop lain. Jika folder tersebut telanjur ikut
tersalin dan tidak dapat dipakai, `start.bat` akan membuatnya ulang secara otomatis.
Data aplikasi berada di `instance/servicehub.db`; salin file itu bersama folder
`instance/uploads` hanya jika data lama memang ingin ikut dipindahkan.

Jika jendela langsung tertutup, jalankan `start.bat` dari Command Prompt agar pesan
error tetap terlihat.

## Menjalankan aplikasi secara manual

```powershell
python -m pip install -r requirements.txt
python seed.py
python wsgi.py
```

Disarankan memakai virtual environment baru pada setiap laptop. Jangan menjalankan
aplikasi dengan Python/Anaconda global karena versi paket dari project lain dapat
saling bertabrakan.

Buka `http://127.0.0.1:8000`. Akun demo:

- Admin: `admin@servicehub.local`
- Agent: `agent@servicehub.local`
- Employee: `employee@servicehub.local`
- Password seluruh akun: `Password123!`

Mode development tidak mengaktifkan debugger kecuali `FLASK_DEBUG=1` diatur secara eksplisit.

`python run.py` tetap dapat digunakan untuk server development pada port 5000.

## Menjalankan production

Atur environment minimal berikut:

```powershell
$env:APP_ENV = "production"
$env:SECRET_KEY = "gunakan-secret-random-yang-panjang"
$env:AUTO_CREATE_DB = "0"
python -m flask --app run:app db upgrade
python wsgi.py
```

Waitress akan berjalan pada `127.0.0.1:8000`. Gunakan reverse proxy HTTPS untuk deployment jaringan. Jangan gunakan nilai `SECRET_KEY` contoh.

## Database migration

```powershell
python -m flask --app run:app db current
python -m flask --app run:app db upgrade
```

Database yang sudah ada telah ditandai pada baseline migration. Selalu backup file SQLite sebelum upgrade schema.
Lampiran disimpan di `instance/uploads` secara default. Lokasi ini dapat diubah
melalui environment variable `UPLOAD_FOLDER` dan harus memiliki izin tulis untuk aplikasi.
Timestamp disimpan dalam UTC dan ditampilkan dalam zona waktu `Asia/Jakarta` (WIB).
Zona tampilan dapat diubah melalui environment variable `APP_TIMEZONE`.

## Fitur

- Dashboard metrik tiket, grafik, SLA compliance, dan tiket mendesak
- Nomor tiket otomatis, pencarian, filter, sorting, pagination
- SLA deadline otomatis berdasarkan priority
- Konfigurasi SLA default per priority dan override deadline tiket aktif oleh Admin
- Informasi SLA lengkap terlihat oleh seluruh user yang memiliki akses ke tiket
- Status Open, In Progress, Pending, Resolved, dan Closed
- Komentar publik, catatan internal, serta audit history
- Lampiran tiket PDF, Word, Excel, dan gambar saat membuat tiket maupun dari halaman detail (maksimal total 5 file, masing-masing 10 MB)
- Role Admin, Agent, dan Employee dengan otorisasi backend
- Manajemen user, department, kategori, dan target SLA
- Edit user/role/department, reset password, proteksi admin terakhir, dan audit log management
- Laporan agregat per department, kategori, priority, status, dan agent
- Export CSV dan Excel dengan perlindungan formula injection
- CSRF protection, security headers, validasi foreign key, dan file signature
- Asset Bootstrap, Bootstrap Icons, dan Chart.js disimpan lokal untuk penggunaan intranet

## Pengujian

```powershell
python -m pytest -q
```

Suite mencakup alur role, authorization, attachment, SLA, timezone, report, CSRF, data integrity, dan keamanan export.

## Backup

Hentikan aplikasi sebelum menyalin `instance/servicehub.db` dan folder `instance/uploads`. Simpan keduanya sebagai satu unit agar metadata attachment tetap cocok dengan file fisiknya.
