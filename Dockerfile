# python:3.10-slim tabanlı imaj kullan
FROM python:3.14-slim

# Sistem bağımlılıklarını kur (ffmpeg: whisper için gerekli, libpq-dev ve build-essential: psycopg2 için gerekli)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Çalışma dizinini ayarla
WORKDIR /app

# pip, setuptools ve wheel güncelle (pkg_resources ve whisper kurulumu için gereklidir)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Bağımlılıkları kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tüm Backend dosyalarını kopyala
COPY . . 

# FastAPI portunu dış dünyaya aç
EXPOSE 3000

# Uygulamayı başlat
CMD ["python", "api.py"]
