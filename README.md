# UGR Booking AI

AI destekli e-posta rezervasyon (booking) ayrıştırma, doğrulama ve sorgulama boru hattı.

> **Temel ilke:** *LLM okur ve önerir, Python karar verir.* Model veritabanına doğrudan yazamaz; her değişiklik doğrulama kuralları ve bir "guard" katmanından geçer.

## Mimari

```mermaid
flowchart TB
    subgraph Katman1 ["1. Veri İşleme ve Doğrulama (Satır 1)"]
        direction LR
        A["data/emails/*.txt"] --> B["Preprocessor<br/>alıntı/HTML temizliği, risk bayrakları, hash"]
        B --> C["LLM Parser<br/>Ollama qwen2.5:7b, yapılandırılmış JSON"]
        C --> D["Validator<br/>master data, tarih/sayı normalizasyonu, iş kuralları"]
        D --> E{"Guard<br/>agent.py"}
    end

    subgraph Katman2 ["2. Depolama ve Arayüz (Satır 2)"]
        direction LR
        F[("SQLite<br/>source_emails · bookings<br/>booking_events · processing_log")] --> H["db/repository.py<br/>ortak sorgu katmanı"]
        H --> I["Streamlit Dashboard"]
        H --> J["Chatbot<br/>chat_router.py"]
        J -->|filtre senkronu| I
    end

    %% Alt satıra geçiş bağlantıları
    E -->|güvenli| F
    E -->|reddedildi| G["NEEDS_REVIEW / VALIDATION_REQUIRED"]
    G --> F
```

| Katman | Dosya | Sorumluluk |
|---|---|---|
| Ön işleme | `pipeline/preprocessor.py` | Başlık ayrıştırma, kronolojik sıra, alıntı/HTML temizliği, enjeksiyon örüntüsü, ek referansı, hash kopya |
| LLM | `pipeline/llm_parser.py` | Ollama ile yapılandırılmış çıkarım (`FakeLLMParser` testler için) |
| Doğrulama | `pipeline/validator.py`, `normalize.py`, `master_data.py` | Müşteri/liman çözümleme, tarih/sayı normalizasyonu, bloklayan ve uyarı bayrakları, durum kararı |
| Guard + araçlar | `pipeline/agent.py`, `tools/booking_ops.py` | `create/update/cancel_booking`; model önerisi guard'dan geçmeden yazılmaz, her karar `processing_log`'a düşer |
| Depolama | `models/orm.py`, `db/connection.py` | 4 tablo, her booking `source_email_id` ile kaynağına bağlı |
| Sorgu katmanı | `db/repository.py` | Dashboard ve chatbot **aynı** fonksiyonları çağırır, sayılar tutarlıdır |
| Arayüz | `ui/app.py`, `ui/chat_router.py`, `app.py` | KPI, filtreler, tablo, İnceleme Kuyruğu, grafikler, chatbot |

## Kurulum

```bash
# Sanal ortamı kur ve bağımlılıkları senkronize et (varsayılan: SQLite)
uv sync --extra dev

# (Opsiyonel) İleride PostgreSQL ile çalıştırmak isterseniz:
# uv sync --extra dev --extra postgres

# Modeli indir (Ollama sunucusu çalışıyor olmalı)
ollama pull qwen2.5:7b
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

### Veri paketi

Case veri seti gizli olduğu için repoda **yoktur** (`data/` git-ignored). `DATA_PATH` altında şu yapı beklenir:

```text
data/
├── emails/            # EMAIL-001.txt ... EMAIL-034.txt
├── manifest.json      # e-posta sırası / received_at
└── master_data/       # customers.json, ports.json, booking_schema.json
```

Sunum/test için beklenen sonuçlar `plan/expected_outcomes.json` (golden set) dosyasındadır; o da git-ignored'dır.

## Çalıştırma

```bash
# 1) E-postaları işle (gerçek Ollama)
uv run python scripts/run_pipeline.py

#    Kaydedilmiş LLM yanıtlarını yeniden kullan (Ollama gerekmez)
uv run python scripts/run_pipeline.py --reuse-llm-cache
#    İlk gerçek koşuda yanıtları cache'e yaz
uv run python scripts/run_pipeline.py --reuse-llm-cache --write-cache

# 2) Sonucu golden set ile karşılaştır
uv run python scripts/eval_real_llm.py --output docs/eval_results.md

# 3) Dashboard + chatbot
uv run streamlit run app.py
```

Chatbot, Ollama erişilemezse veya yönlendirme geçersiz dönerse regex tabanlı yedek yönlendiriciye düşer; case'in 4 örnek sorusu bu durumda da çalışır.

## Testler

```bash
uv run pytest
```

- Birim testleri: normalizasyon, ön işleme, doğrulama, guard/araçlar, repository, chat router.
- `tests/test_pipeline_e2e.py`: 34 e-postayı geçici bir DB'ye işler ve **yanlış READY olmadığını** doğrular. Gizli veri veya LLM cache yoksa atlanır.
  Golden set ile tam eşleşme kontrolleri taze bir cache ister: `E2E_STRICT_GOLDEN=1 uv run pytest tests/test_pipeline_e2e.py`.

## Varsayımlar

- Müşteri ve liman eşleştirme yalnızca master data üzerinden, tam (fold edilmiş) eşleşmeyle yapılır; bulanık eşleştirme yoktur. Gönderen alan adı müşteriyi tek başına çözmez, yalnızca ipucudur.
- Emin olunmayan değer tahmin edilmez; alan boş bırakılır ve `VALIDATION_REQUIRED` olarak işaretlenir.
- Toplam booking sayısı yalnızca `bookings` tablosunu sayar (yetim, durum sorgusu ve rezervasyon-dışı e-postalar dahil değildir).
- Chatbot sayıları şablonla üretilir; LLM sayı yazmaz. Kayıtta olmayan bilgi için sabit yanıt döner: *"Bu bilgi mevcut kayıtlarda bulunmuyor."*

## Bilinen Limitler ve Eksiklikler

Aşağıdaki tablo, sistemin mevcut sınırlarını, nedenlerini ve sonraki aşamadaki olası çözümlerini özetlemektedir:

| Eksiklik / Sınır | Kök Neden | Olası Çözüm | Örnek Senaryo |
|---|---|---|---|
| **Düzeltme E-postasını Güncelleme Olarak Yakalayamamak** (014, 015) | 7B model konuşma bağlamını kaçırdı; düzeltmeyi yeni talep sandı. | Few-shot prompting veya daha güçlü bir model (GPT-4o/Claude) kullanımı. | *"Correction: please update date to 15.10.2026"* |
| **Yetim Güncellemeleri Bulanık (Fuzzy) Eşleştirememek** (003, 007) | Sistem yalnızca tam referans eşleşmesi arar; bulamayınca kaydı doğrudan `ORPHAN` yapar. | Müşteri ve tarih penceresi üzerinden aday kaydı puanlayan Fuzzy Matcher ile operatör önerisi sunmak. | *"Lütfen dünkü rezervasyonumuzun yükleme tarihini değiştirin"* |
| **Ek Dosyaların İçeriğini Okuyamamak** (029) | Sistemde bir OCR veya PDF/Excel ayrıştırıcı boru hattı (pipeline) yok. | Boru hattının başına `pypdf`, OCR veya multimodal LLM eklenerek ekleri metne dökmek. | *"Please find attached our booking request and VIN list"* |
| **Bilinmeyen Domainden Gelen Gerçek Müşteriler** | Gönderici/müşteri uyuşmazlığı kuralı (`SENDER_CUSTOMER_MISMATCH`) doğrudan şüpheli sayar. | Kişisel e-postaları iletişim listesine eklemek veya şirket domainine doğrulama kodu göndermek. | Kayıtlı ABC Shipping yetkilisinin kişisel/farklı bir adresten talep atması. |

> **Güvenlik Notu:** Bu 4 sınırın tamamı sistemin güvenlik zırhı sayesindedir; model/veri yetersiz kaldığında hiçbir hatalı kayıt `READY` yapılmaz, doğrudan `VALIDATION_REQUIRED` veya `ORPHAN` karantinasına alınır.

- **Golden set uyumu:** 34 e-postanın 14'ü golden set ile tam eşleşiyor. Sapmaların çoğu uyarı bayraklarında (ör. `QUANTITY_FROM_VIN_COUNT`) ve model kaynaklı alan farklarında; ayrıntı için `docs/eval_results.md`. Güvenlik özelliği ölçüldü: beklenen 9 READY kayıt dışında hiçbir kayıt READY değil.
- **Güncelleme/iptal testleri sentetiktir:** Veri setinde yalnızca 013 gerçek bir kaydı (D100) bulur; diğer akışlar sentetik create→update/cancel testleriyle doğrulanır.
- **Canlı posta kutusu, kimlik doğrulama ve bulut dağıtımı kapsam dışıdır.**

## Üretime Geçmeden Önce Yapılacaklar

1. **Veritabanı Göçü (Migration):** PostgreSQL + Alembic entegrasyonu.
2. **Operatör Arayüzü:** `NEEDS_REVIEW` durumundaki kayıtlar için insan onay ve düzenleme ekranı.
3. **Ek Dosya Pipeline'ı:** OCR ve `pypdf` / multimodal LLM desteği ile PDF/Excel içeriklerinin ayrıştırılması.
4. **Gelişmiş Eşleştirme:** Yetim güncellemeler için kural tabanlı Fuzzy Matcher entegrasyonu.
5. **Gözlemlenebilirlik (Observability):** Yapılandırılmış loglama (Structured Logging) ve metrik takibi.