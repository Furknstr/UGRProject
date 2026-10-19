# UGR Booking AI

AI destekli e-posta rezervasyon (booking) ayrıştırma, doğrulama ve sorgulama boru hattı.

## Kurulum

```bash
# Sanal ortamı kur ve bağımlılıkları senkronize et
uv sync --extra dev --extra postgres
```

## Yapılandırma

`.env` dosyasını `.env.example` üzerinden düzenleyebilirsiniz:

```env
DATABASE_URL=sqlite:///./ugr_booking.db
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
DATA_PATH=./data
LOG_LEVEL=INFO
```
