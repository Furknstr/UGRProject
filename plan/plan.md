# UGR Booking AI — Birleşik Uygulama Planı (v3 — Final)

> **Durum:** PostgreSQL kurulu değil → **SQLite varsayılan**, `DATABASE_URL` env var ile PostgreSQL isteğe bağlı.
> LLM: Ollama + `qwen2.5:7b` | Süre tahmini: ~3 gün (~14.5 saat aktif çalışma)
> Son güncelleme: 10 kritik düzeltme işlendi.

---

## Mimari Özeti

```
data/emails/          data/master_data/
     │                      │
     ▼                      │
┌────────────────┐          │
│ 1. Preprocessor│          │
│  - encoding    │          │
│  - quote strip │          │
│  - dedup hash  │          │
└───────┬────────┘          │
        │                   │
        ▼                   │
┌────────────────┐          │
│ 2. LLM Parser  │          │
│  Ollama        │          │
│  qwen2.5:7b    │          │
│  format=JSON   │          │
│  temp=0        │          │
│  → ham değerler│          │
└───────┬────────┘          │
        │                   ▼
        ▼           ┌───────────────┐
┌────────────────┐  │ Master Data   │
│ 3. Schema      │  │ customers.json│
│  Validator     │◄─┤ ports.json    │
│  (Pydantic)    │  └───────────────┘
│  tip / tarih   │
│  VIN count     │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ 4. Business    │
│  Validator     │
│  - alias → ID  │  (Python, fuzzy yok)
│  - port code   │
│  - orphan chk  │
│  - domain chk  │
│  - dupe chk    │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ 5. Agent       │
│  Orchestrator  │
│  - guard: val  │
│    geçmeden    │
│    create yok  │
│  - Python tool │
│    dispatch    │
└───────┬────────┘
        │
        ▼
┌─────────────────────────────────┐
│     SQLite (varsayılan)         │
│   / PostgreSQL (isteğe bağlı)  │
│                                 │
│  source_emails  │  bookings     │
│  booking_events │ processing_log│
└────────┬───────────────┬────────┘
         │               │
         ▼               ▼
  ┌──────────┐   ┌──────────────┐
  │Dashboard │   │   Chatbot    │
  │Streamlit │   │ Yapısal sorgu│
  │filtreler │   │ (SQL yok)    │
  └──────────┘   └──────────────┘
```

---

## Veri Modeli (3 + 1 Tablo)

### `source_emails`
Ham e-posta kaydı. Her gelen e-posta için bir satır.

```
id                   INTEGER PK
email_id             TEXT UNIQUE          -- "EMAIL-001"
file_path            TEXT
received_at          DATETIME
subject              TEXT
sender               TEXT
body_raw             TEXT                 -- orijinal ham metin
body_clean           TEXT                 -- preprocessor çıktısı
content_hash         TEXT                 -- birebir kopya tespiti için SHA-256 (yalnızca aynı dosyalar)
has_quote            BOOLEAN              -- alıntılı eski yazışma var mı
has_attachment_ref   BOOLEAN              -- "details attached" gibi ifade var mı
encoding_warning     TEXT                 -- bozuk karakter tespit mesajı (EMAIL-026)
classified_intent    TEXT                 -- CREATE/UPDATE/CANCEL/STATUS/NON_BOOKING/UNKNOWN
processing_status    TEXT DEFAULT 'RECEIVED'
error_message        TEXT
created_at           DATETIME
```

### `bookings`
Sadece gerçek booking kayıtları. NON_BOOKING ve STATUS e-postaları burada yer almaz.

```
id                   INTEGER PK
booking_ref          TEXT                 -- BOOKINGREF-X001 (null = henüz atanmamış)
source_email_id      TEXT FK → source_emails.email_id
split_index          INTEGER DEFAULT 0    -- tek e-postada 2 booking → 0 ve 1
customer_id          TEXT                 -- CUST-001 (çözümlenmiş, null = belirsiz)
customer_raw         TEXT                 -- LLM'in çıkardığı ham değer
pol                  TEXT                 -- ITGOA (çözümlenmiş port kodu)
pol_raw              TEXT                 -- "GENOA", "Genoa" vs.
pod                  TEXT
pod_raw              TEXT
cargo_description    TEXT
quantity             INTEGER
vin_list             TEXT                 -- JSON array string
weight_kg_per_unit   REAL
length_m             REAL
width_m              REAL
height_m             REAL
requested_date       TEXT                 -- YYYY-MM-DD, geçersizse null
requested_date_raw   TEXT                 -- "31/02/2026" gibi ham değer
vessel               TEXT
voyage               TEXT
rate_text            TEXT
contact_name         TEXT
contact_email        TEXT
processing_status    TEXT DEFAULT 'RECEIVED'
  -- RECEIVED | PARSED | VALIDATION_REQUIRED | READY | ASSIGNED | CANCELLED
validation_flags     TEXT
  -- JSON array of objects: [{"field": "pod", "code": "UNKNOWN_PORT", "raw": "DUBAI", "suggestion": null}]
  -- Alan adı + hata kodu + ham değer + öneri → dashboard "neden VALIDATION_REQUIRED" gösterir
raw_llm_output       TEXT                 -- JSON, debug için
created_at           DATETIME
updated_at           DATETIME
```

### `booking_events`
İş geçmişi (audit trail). Her CREATE / UPDATE / CANCEL bir satır.

> **Düzeltme #4:** `booking_ref`'e değil, `booking_id`'ye (FK) bağlı. Ref'siz booking'lerde
> (EMAIL-002, 009, 016 vb.) de olay kaydedilebilir.

```
id              INTEGER PK
booking_id      INTEGER FK → bookings.id   -- ref yerine PK ile bağ
booking_ref     TEXT                       -- ek bilgi, null olabilir
event_type      TEXT                       -- CREATED | UPDATED | CANCELLED | STATUS_CHECKED
source_email_id TEXT FK → source_emails.email_id
delta           TEXT                       -- JSON: hangi alanlar değişti
notes           TEXT
created_at      DATETIME
```

### `processing_log`
Boru hattı teknik logu. Her aşama için süre ve hata.

```
id              INTEGER PK
email_id        TEXT
stage           TEXT                     -- preprocessor | llm_parser | validator | agent
status          TEXT                     -- success | error | skipped
message         TEXT
duration_ms     INTEGER
created_at      DATETIME
```

---

## Klasör Yapısı

```
ugr_project/
├── pyproject.toml
├── .env.example
├── .gitignore                   # data/ ve .env eklenmeli
├── README.md
├── plan/
│   └── plan.md                  # bu dosya
├── data/                        # .gitignore'da (restricted)
│   ├── emails/
│   ├── master_data/
│   └── manifest.json
├── src/
│   └── ugr_booking/
│       ├── __init__.py
│       ├── config.py            # Ayarlar: DATABASE_URL, OLLAMA_HOST, OLLAMA_MODEL
│       ├── models/
│       │   ├── orm.py           # SQLAlchemy ORM sınıfları (4 tablo)
│       │   └── schemas.py       # Pydantic şemaları (input/output/validation)
│       ├── pipeline/
│       │   ├── preprocessor.py  # Aşama 1
│       │   ├── llm_parser.py    # Aşama 2
│       │   ├── validator.py     # Aşama 3 + 4
│       │   └── agent.py         # Aşama 5
│       ├── tools/
│       │   ├── lookup.py        # lookup_customer(), lookup_port()
│       │   └── booking_ops.py   # create_booking(), update_booking(), cancel_booking()
│       ├── db/
│       │   ├── connection.py    # Engine + session factory
│       │   └── repository.py    # CRUD + yapısal sorgu fonksiyonları
│       ├── master_data.py       # customers.json / ports.json yükleyici
│       └── ui/
│           └── app.py           # Streamlit: Dashboard + Chatbot
├── scripts/
│   └── run_pipeline.py          # manifest.json'u okur, tüm e-postaları işler
└── tests/
    ├── conftest.py              # Fake LLM fixture, test DB, örnek e-postalar
    ├── test_preprocessor.py
    ├── test_validator.py
    ├── test_agent.py
    └── test_pipeline_e2e.py    # Golden set testi
```

---

## Aşama Detayları

---

### Aşama 1 — Preprocessor

**Sorumluluklar:**
- `manifest.json`'dan her e-postayı `received_at` sırasına göre yükle (güncelleme/iptaller doğru sıraya bağlı)
- Ham metni `body_raw`'a kaydet
- SHA-256 hash ile **birebir** kopya tespiti: aynı dosya içeriği → `DUPLICATE`
- Alıntılanmış eski yazışmayı soy — boşluk ve büyük/küçük harf toleranslı regex:
  ```python
  QUOTE_PATTERNS = [
      r'^\s*>.*',                                    # ">" ile başlayan satırlar
      r'-{3,}\s*[Pp]revious [Mm]essage\s*-{3,}',    # EMAIL-015
      r'-{3,}\s*[Oo]riginal [Mm]essage\s*-{3,}',    # EMAIL-030
      r'(?m)^On .+wrote:',
  ]
  # Eşleşen satır ve sonrası body_clean'e dahil edilmez
  # test fixture: gerçek EMAIL-015.txt ve EMAIL-030.txt kullan
  ```
- "Details attached" / "see attachment" / "please find attached" tespiti → `has_attachment_ref = True`
- Encoding sorunu: replacement character (`\ufffd` / `\x3f` kayıp) tespiti → `encoding_warning` doldur, **düzeltmeye çalışma** (EMAIL-026: orijinal harf zaten kaybolmuş, `chardet`/`ftfy` çalışmaz)
- `is_likely_booking()` yalnızca işaret koyar — karar vermez, pipeline durmaz

**Kritik kural:** `is_likely_booking = False` olan e-posta pipeline'dan çıkarılmaz.
Yanlış negatif = sessiz veri kaybı.

> **Düzeltme #1 — Hash kopyası EMAIL-008/012'yi yakalamaz!**
> EMAIL-012'nin konusu "RE: BOOKINGREF-D100 - resend" ve gövdesinde "Resending because..." var.
> Hash farklı çıkar. **Semantik kopya tespiti Aşama 4 (Business Validator)'a taşındı.**

---

### Aşama 2 — LLM Parser

**Ollama yapılandırması:**
```python
ollama.chat(
    model="qwen2.5:7b",
    messages=[...],
    format=BookingExtraction.model_json_schema(),  # Pydantic şemasını ver
    options={"temperature": 0, "num_ctx": 4096}
)
```

**System prompt ilkeleri:**
- LLM'e **müşteri listesi verilmez**, **liman listesi verilmez** → model ham değer çıkarır, çözümleme Python'da
- "Eğer bir bilgi e-postada açıkça yoksa `null` döndür" — icat etme talimatı
- "Sen yalnızca çıkarım yapıyorsun, doğrulama veya düzeltme yapmıyorsun"

**LLM çıktı şeması:**
```python
class SingleBookingRaw(BaseModel):
    booking_ref: str | None
    customer_raw: str | None          # Ham çıktı, alias dahil
    pol_raw: str | None
    pod_raw: str | None
    cargo_description: str | None
    quantity: int | None
    vin_list: list[str]
    weight_raw: str | None            # "7,330 KG" → ham bırak
    length_raw: str | None
    width_raw: str | None
    height_raw: str | None
    requested_date_raw: str | None    # "31/02/2026" → ham bırak
    vessel: str | None
    voyage: str | None
    rate_text: str | None
    contact_name: str | None
    contact_email: str | None
    parser_notes: str | None          # LLM belirsizliği not düşsün

class BookingExtraction(BaseModel):
    message_intent: Literal[
        "CREATE_BOOKING","UPDATE_BOOKING","CANCEL_BOOKING",
        "STATUS_INQUIRY","NON_BOOKING","UNKNOWN"
    ]
    bookings: list[SingleBookingRaw]  # Çoğul destek (EMAIL-017 gibi)
```

**Hata yönetimi:**
- `format` ile JSON kısıtlandığında parse hatası büyük ölçüde olmaz
- try/except → `processing_status = PARSED_ERROR`, pipeline durmaz, sonraki e-postaya geç

---

### Aşama 3 — Schema Validator (Pydantic)

LLM'in ham string çıktılarını temizler ve tip dönüşümü yapar.
Başarısız olan alan `null` olur ve `validation_flags`'e `{field, code, raw, suggestion}` nesnesi eklenir.

**Sayı dönüşümü kuralları:**

| Ham Değer | Dönüşüm | Notlar |
|---|---|---|
| `"7,330 KG"` | `7330.0` | Türkçe/Avrupa binlik ayırıcı |
| `"7.330,50"` | `7330.50` | virgül ondalık, nokta binlik |
| `"12,400"` | `12400.0` veya `12.4`? | Belirsiz → **bağlam kontrolü**: ağırlık için `>1000` makul, genişlik için `<5m` beklenir — makullük aralığı dışındaysa flag |
| `"2.450"` | `2.45` (m) veya `2450`? | genişlik bağlamında `2.45m` makul, `2450m` değil → normalize + flag |
| `"620 cm"` | `6.20` m | birim açıkça cm ise çevir |
| birim belirsiz | ham bırak | flag `UNIT_AMBIGUOUS` |

**Tarih dönüşümü kuralları:**
- Tarih formatı tahmini: `GG/AA/YYYY` → önce dene; `AA/GG/YYYY` → sadece `GG > 12` ise zorunlu
- Geçersiz tarih (31/02/2026) → `null` + flag `INVALID_DATE`
- Belirsiz tarih ("SEPTEMBER SAILING") → `null` + flag `AMBIGUOUS_DATE`
- Geçmişte kalan tarih kontrolü `received_at` referanslı: `requested_date < received_at - 1 gün` → flag `DATE_IN_PAST`
- Prompt kuralı: LLM boyut/ağırlık değerlerini **birim ile birlikte ham string olarak korusun** ("620 cm", "7,330 KG"), Python dönüştürsün

**Diğer kontroller:**

| Koşul | Flag |
|---|---|
| `quantity ≠ len(vin_list)` (ikisi de dolu) | `VIN_QUANTITY_MISMATCH` |
| `requested_date` parse edilemez | `INVALID_DATE` |

**READY kararı için zorunlu alanlar (hepsi dolu ve flag'siz olmalı):**
```
customer_id (çözümlenmiş) | pol | pod | cargo_description | quantity | requested_date
```
Bunlardan biri `null`, çözümsüz veya bayraklıysa → `VALIDATION_REQUIRED`. Sessiz geçiş yok.

---

### Aşama 4 — Business Validator

**Müşteri çözümleme:**
```
1. Tam eşleşme: "ABC Shipping" → CUST-001
2. Alias eşleşme (case + unicode normalize: NFKD + lower): "ABC ship" → CUST-001
3. Eşleşme yok → customer_id = null, flag = {field:"customer", code:"UNKNOWN_CUSTOMER", raw:"...", suggestion:null}
```
Fuzzy match yok. En yakın alias öneri olarak `suggestion` alanına yazılır, otomatik kabul edilmez.

**Liman çözümleme (Düzeltme #6 — iç çelişki giderildi):**
```
1. Port kodu tam: "ITGOA" → ITGOA
2. Alias eşleşme (NFKD normalize + lower + strip): "Genova" → ITGOA
3. "JEDAH" → SAJED  (alias listesinde kayıtlı)
4. Aksan sadeleştirme güvenli kabul edilir:
   "SÃGUNTO" → unicode normalize → "SAGUNTO" → ESSAG  ✅ ÇÖZÜMLENIR
5. Kayıp karakter içeren değer (source_email encoding_warning=True ve
   ilgili alan bu e-postadan geliyorsa):
   "JEDD?H", "JEDD\uFFFDH" → VALIDATION_REQUIRED, öneri: SAJED
   (Bilgi kaybı olduğu kesin, otomatik kabul edilemez)
6. Eşleşme yok → port = null, flag = {code:"UNKNOWN_PORT", ...}
```

**Semantik kopya tespiti (Düzeltme #1 — hash yerine fingerprint):**
- Hash kopyası: preprocessor'da aynı `content_hash` → `DUPLICATE` (birebir aynı dosya)
- Semantik kopya: Aşama 4'te `(customer_id, pol, pod, quantity, frozenset(vin_list))` tuple'ı → zaten DB'de varsa `POSSIBLE_DUPLICATE` (EMAIL-008/012 bu yolla yakalanır)
- Aynı `booking_ref` ile ikinci CREATE → `POSSIBLE_DUPLICATE`

**Kanıt kontrolü (Düzeltme #3 ek):**
- LLM'in çıkardığı VIN, booking_ref gibi değerlerin `body_clean` içinde **gerçekten geçip geçmediğini** kontrol et
- Geçmiyorsa → flag `HALLUCINATED_VALUE`, değer null'lanır
- Bu, prompt injection (EMAIL-031: uydurulmuş POD "DUBAI") dahil tüm hallüsinasyonları yakalar

**Yetim (orphan) tespiti:**
- UPDATE/CANCEL intent'inde `booking_ref` null → `ORPHAN_NO_REF`
- `booking_ref` var ama DB'de yok → `ORPHAN_REF_NOT_FOUND`
- Yetim kayıtlar `source_emails`'te kalır, `bookings` tablosuna girmez
- Dashboard'da **"İnceleme Kuyruğu"** panelinde gösterilir (Aşama 6)

**Gönderen domain kontrolü:**
- E-posta domain'i ile çözümlenen müşteri uyuşmuyorsa → `SENDER_CUSTOMER_MISMATCH`
- Domain → müşteri eşlemesi `config/domain_map.json` dosyasına konur (README'de "veri setinden türetilmiş varsayım" notu)

**Ek referans uyarısı:**
- `has_attachment_ref = True` → flag `MISSING_ATTACHMENT`, status = VALIDATION_REQUIRED

**Prompt injection savunması (Düzeltme #3):**
- Genişletilmiş örüntüler (EMAIL-031 gerçek içeriğine göre):
  ```python
  INJECTION_PATTERNS = [
      r'ignore\s+(all|previous|any)\s+.*(rule|instruction)',  # EMAIL-031
      r'system\s+note[:\s]',
      r'you\s+are\s+now\s+',
      r'disregard\s+.*(above|previous)',
      r'new\s+instruction',
  ]
  ```
- `SUSPICIOUS_CONTENT` flag'i → **processing_status = VALIDATION_REQUIRED** (sadece log değil, READY'yi engeller)
- Asıl koruma: kanıt kontrolü (POD="DUBAI" master'da yok, validator zaten yakalardı)

---

### Aşama 5 — Agent Orchestrator

**Guard kuralı:**
```python
def can_create(booking: BookingDraft) -> bool:
    # VALIDATION_REQUIRED veya SUSPICIOUS_CONTENT olan booking'ler create'e GEÇEMEz
    return booking.processing_status == "READY"
```

**Düzeltme #7 — Görünür AI→Tool etkileşimi:**

LLM çıktısına `proposed_action` eklendi. Guard her kararı loglar. Sunum için tam izlenebilirlik.

```python
class BookingExtraction(BaseModel):
    message_intent: ...
    bookings: list[SingleBookingRaw]
    proposed_action: str | None
    # Örnek: "create_booking" veya "update_booking(ref=BOOKINGREF-X001)"
    # LLM öneriyor, Python karar veriyor
```

Guard log formatı (her karar `processing_log`'a yazılır):
```
stage=agent, email=EMAIL-031
  proposed_action: create_booking
  guard_decision: REJECTED
  reason: SUSPICIOUS_CONTENT flag set

stage=agent, email=EMAIL-010
  proposed_action: create_booking
  guard_decision: REJECTED
  reason: VALIDATION_REQUIRED (fields: customer_id, pod)

stage=agent, email=EMAIL-019
  proposed_action: create_booking
  guard_decision: APPROVED
  booking_id: 42
```

**Dispatch (Python karar verir, LLM öneriyor):**
```python
match intent:
    case "CREATE_BOOKING":
        for draft in drafts:
            lookup_customer(draft.customer_raw)
            lookup_port(draft.pol_raw, draft.pod_raw)
            validate(draft)
            log_guard_decision(draft)          # her zaman logla
            if can_create(draft):
                create_booking(draft)
                log_event(booking_id, "CREATED")
            else:
                save_as_validation_required(draft)

    case "UPDATE_BOOKING":
        if not draft.booking_ref or not booking_exists(draft.booking_ref):
            mark_orphan(draft)                 # source_emails'te kalır
        else:
            booking_id = get_booking_id(draft.booking_ref)
            update_booking(booking_id, delta)
            log_event(booking_id, "UPDATED", delta)

    case "CANCEL_BOOKING":
        if not booking_exists(draft.booking_ref):
            mark_orphan(draft)
        else:
            booking_id = get_booking_id(draft.booking_ref)
            cancel_booking(booking_id)
            log_event(booking_id, "CANCELLED")

    case "STATUS_INQUIRY":
        get_booking_status(draft.booking_ref)
        # bookings tablosuna yeni satır eklenmez
        log_event(None, "STATUS_CHECKED", notes=draft.booking_ref)

    case "NON_BOOKING":
        # sadece source_emails'e kaydet, bookings'e girme
        pass
```

> **Düzeltme #10 — Güncelleme/İptal unit testi:**
> Veri setinde yalnızca EMAIL-013 (D100 iptali) gerçek anlamda çalışır.
> Diğer 6 UPDATE/CANCEL yetimdir (temel kayıt yok).
> `test_agent.py`'de **sentetik fixture** ile test yazılacak:
> önce `create_booking()` çağır, ardından `update_booking()` / `cancel_booking()`.
> README'de "veri setinde 6 güncelleme/iptal e-postası yetim çıkmaktadır" açıkça belirtilecek.

---

### Aşama 6 — Dashboard + Chatbot (Streamlit)

**Dashboard:**
```
Sidebar: Filtreler (müşteri, POL, POD, status, tarih aralığı)

┌──────────────────────────────────────────────────────┐
│ [Toplam Booking] [READY] [VAL.REQUIRED] [CANCELLED]  │
├───────────────────────┬──────────────────────────────┤
│ Status dağılımı       │ Müşteri başına booking        │
│ (bar chart)           │ (bar chart)                  │
├───────────────────────┴──────────────────────────────┤
│ Booking tablosu (filtrelenmiş, sıralanabilir)        │
│ Tıklanan satır → detay paneli (validation_flags dahil│
├──────────────────────────────────────────────────────┤
│ ⚠️ İnceleme Kuyruğu (Düzeltme #9)                   │
│  Yetimler | Ek eksik | Şüpheli içerik | Boş gövde   │
│  source_emails tablosundan filtrelenir               │
├──────────────────────────────────────────────────────┤
│ 💬 Chatbot                                           │
│ [____________________________________] [Gönder]      │
└──────────────────────────────────────────────────────┘
```

> **Düzeltme #9:** Yetim/sorunlu e-postalar `bookings` tablosuna girmediğinden
> "doğrulama bekleyenler" listesinde görünmüyordu. `source_emails`'ten ayrı
> "İnceleme Kuyruğu" paneli bu boşluğu kapatır.

**Chatbot — text-to-SQL YOK (Düzeltme #8):**
```python
# Repository'deki yapısal sorgu fonksiyonları
def count_by_customer(customer_id: str | None) -> dict
def filter_bookings(pol=None, pod=None, status=None, customer_id=None) -> list
def get_validation_required() -> list
def get_latest_n(n: int) -> list
def get_by_ref(booking_ref: str) -> BookingRecord | None
def summary_stats() -> dict
```

Chatbot akışı:
1. LLM soruyu analiz edip hangi fonksiyon + parametreyi belirler (JSON döndürür)
2. **Chatbot'taki "ABC Shipping" / "Izmir" ifadeleri pipeline'daki aynı resolver'dan geçer**
   (master_data.py) — farklı sayı riski sıfır
3. Python fonksiyonu çağırır, veriyi getirir
4. **Sayısal cevaplar şablonla üretilir** — LLM'e yazdırılmaz:
   ```python
   # LLM sayı döndürmez, Python şablonu kullanır
   template = "ABC Shipping için {total} booking: {ready} READY, {val_req} VALIDATION_REQUIRED"
   ```
5. Serbest metin özeti ("son 3 booking'i özetle") için LLM kullanılır
6. Dashboard filtreleri güncellenir (`st.session_state`)
7. Veri yoksa sabit yanıt: "Bu bilgi mevcut kayıtlarda bulunmuyor" — model bilgisinden cevap vermez

LLM chatbot system prompt: "Aşağıdaki fonksiyonlardan birini seç, parametrelerini JSON döndür. SQL yazma, sayı üretme."

---

## Test Stratejisi

### Fake LLM Fixture
```python
# tests/conftest.py
class FakeLLMParser:
    """Gerçek Ollama çağırmaz. Testler hızlı + deterministik."""
    def __init__(self, responses: dict[str, dict]):
        self.responses = responses  # email_id → extraction dict

    def parse(self, email_doc) -> BookingExtraction:
        return BookingExtraction(**self.responses[email_doc.email_id])
```

### Golden Set (Düzeltme: 34 e-postanın tamamı kapsanıyor)
`tests/fixtures/expected_outcomes.json` — tüm e-postalar:
```json
{
  "EMAIL-001": {"intent":"CREATE_BOOKING","customer_id":"CUST-001","pol":"ITGOA","pod":null,"flags":["AMBIGUOUS_POD"],"quantity":3,"status":"VALIDATION_REQUIRED"},
  "EMAIL-002": {"intent":"CREATE_BOOKING","customer_id":"CUST-002","pol":null,"quantity":3,"status":"VALIDATION_REQUIRED"},
  "EMAIL-003": {"intent":"UPDATE_BOOKING","booking_ref":"BOOKINGREF-X001","flags":["ORPHAN_REF_NOT_FOUND"]},
  "EMAIL-004": {"intent":"STATUS_INQUIRY","status":"not_in_bookings"},
  "EMAIL-005": {"intent":"CREATE_BOOKING","customer_id":"CUST-001","quantity":10,"status":"VALIDATION_REQUIRED"},
  "EMAIL-006": {"intent":"NON_BOOKING","status":"not_in_bookings"},
  "EMAIL-007": {"intent":"UPDATE_BOOKING","flags":["ORPHAN_REF_NOT_FOUND"]},
  "EMAIL-008": {"intent":"CREATE_BOOKING","booking_ref":"BOOKINGREF-D100"},
  "EMAIL-009": {"intent":"CREATE_BOOKING","customer_id":"CUST-001","pol":"GRPIR","pod":"SAJED"},
  "EMAIL-010": {"intent":"CREATE_BOOKING","pol":"SIKOP","pod":"SAJED"},
  "EMAIL-011": {"intent":"CREATE_BOOKING","pol":"ITGOA","pod":"SAYNB","quantity":4},
  "EMAIL-012": {"intent":"CREATE_BOOKING","flags":["POSSIBLE_DUPLICATE"],"booking_ref":"BOOKINGREF-D100"},
  "EMAIL-013": {"intent":"CANCEL_BOOKING","booking_ref":"BOOKINGREF-D100"},
  "EMAIL-014": {"intent":"UPDATE_BOOKING","flags":["ORPHAN_REF_NOT_FOUND"]},
  "EMAIL-015": {"intent":"UPDATE_BOOKING","booking_ref":"BOOKINGREF-A220","flags":["ORPHAN_REF_NOT_FOUND"]},
  "EMAIL-016": {"intent":"CREATE_BOOKING","pol":"GRPIR","pod":"SAYNB"},
  "EMAIL-017": {"intent":"CREATE_BOOKING","booking_count":2},
  "EMAIL-018": {"intent":"CREATE_BOOKING","flags":["UNKNOWN_CUSTOMER","AMBIGUOUS_CARGO"],"status":"VALIDATION_REQUIRED"},
  "EMAIL-019": {"intent":"CREATE_BOOKING","pol":"ESBCN","pod":"SAJED","quantity":4},
  "EMAIL-020": {"intent":"CREATE_BOOKING","flags":["INVALID_DATE"],"status":"VALIDATION_REQUIRED"},
  "EMAIL-021": {"intent":"CREATE_BOOKING","pol":"ESSAG","pod":"SAJED","quantity":2},
  "EMAIL-022": {"intent":"CREATE_BOOKING","pol":"ITGOA","pod":"SAJED","quantity":1},
  "EMAIL-023": {"intent":"UPDATE_BOOKING","booking_ref":"BOOKINGREF-A310","flags":["ORPHAN_REF_NOT_FOUND"]},
  "EMAIL-024": {"intent":"STATUS_INQUIRY","status":"not_in_bookings"},
  "EMAIL-025": {"intent":"CREATE_BOOKING","pol":"ITGOA","pod":"SAJED","quantity":3},
  "EMAIL-026": {"intent":"CREATE_BOOKING","flags":["ENCODING_WARNING","UNKNOWN_PORT"],"status":"VALIDATION_REQUIRED"},
  "EMAIL-027": {"intent":"CREATE_BOOKING","pol":"GRPIR","pod":"SAYNB"},
  "EMAIL-028": {"intent":"CREATE_BOOKING","booking_ref":"BOOKINGREF-D200"},
  "EMAIL-029": {"intent":"CREATE_BOOKING","flags":["MISSING_ATTACHMENT"],"status":"VALIDATION_REQUIRED"},
  "EMAIL-030": {"intent":"CANCEL_BOOKING","booking_ref":"BOOKINGREF-X300","flags":["ORPHAN_REF_NOT_FOUND"]},
  "EMAIL-031": {"intent":"CREATE_BOOKING","flags":["SUSPICIOUS_CONTENT","HALLUCINATED_VALUE"],"status":"VALIDATION_REQUIRED"},
  "EMAIL-032": {"intent":"CREATE_BOOKING","booking_ref":"BOOKINGREF-D250"},
  "EMAIL-033": {"intent":"CREATE_BOOKING","pol":"TRIZM","pod":"ITLIV","quantity":3},
  "EMAIL-034": {"intent":"CREATE_BOOKING","pol":"TRIZM","pod":"SAYNB","quantity":2}
}
```

### Test Senaryoları

**`test_preprocessor.py`**
```python
def test_quote_stripped_015()         # "----- Previous message -----" sonrası kesilir (EMAIL-015)
def test_quote_stripped_030()         # "----- Original Message -----" (boşluklu, EMAIL-030)
def test_content_hash_same_file()     # Birebir aynı dosya → DUPLICATE
def test_semantic_dupe_008_012()      # Farklı hash, aynı fingerprint → POSSIBLE_DUPLICATE
def test_encoding_warning_026()       # \uFFFD → uyarı, düzeltme yok
def test_attachment_ref_detected()    # EMAIL-029: "details attached"
```

**`test_validator.py`**
```python
def test_invalid_date_flagged()               # 31/02/2026 → null + INVALID_DATE
def test_ambiguous_date_flagged()             # "SEPTEMBER SAILING" → null + AMBIGUOUS_DATE
def test_date_in_past_flagged()              # received_at > requested_date → DATE_IN_PAST
def test_vin_quantity_mismatch()              # quantity=3, vin=[v1,v2] → VIN_QUANTITY_MISMATCH
def test_unknown_customer_flagged()           # alias eşleşmez → UNKNOWN_CUSTOMER
def test_unknown_port_flagged()               # tanınmayan liman → UNKNOWN_PORT
def test_sagunto_accent_resolves()            # "SÃGUNTO" → ESSAG (aksan normalize)
def test_lost_char_port_validation_req()      # "JEDD?H" → VALIDATION_REQUIRED
def test_sender_customer_mismatch()           # domain ≠ müşteri → SENDER_CUSTOMER_MISMATCH
def test_orphan_update_no_ref()               # UPDATE, ref yok → ORPHAN_NO_REF
def test_orphan_update_ref_not_found()        # UPDATE, ref DB'de yok → ORPHAN_REF_NOT_FOUND
def test_number_format_comma_dot()            # "7,330" → 7330.0
def test_cm_to_m_conversion()                 # "620 cm" → 6.20
def test_ambiguous_number_flagged()           # "2.450" bağlama göre flag
def test_missing_attachment_flagged()         # has_attachment_ref=True → MISSING_ATTACHMENT
def test_hallucinated_value_flagged()         # LLM değeri body'de geçmiyor → HALLUCINATED_VALUE
def test_required_fields_ready()              # tüm zorunlu alanlar dolu → READY
def test_required_fields_missing_makes_val_req() # zorunlu alan eksik → VALIDATION_REQUIRED
```

**`test_agent.py`**
```python
def test_guard_blocks_create_if_validation_required()  # Guard kuralı
def test_guard_blocks_create_if_suspicious()           # SUSPICIOUS_CONTENT de engeller
def test_guard_decision_logged()                       # Her karar processing_log'a gider
def test_non_booking_not_in_bookings_table()           # NON_BOOKING → sadece source_emails
def test_status_inquiry_not_in_bookings_table()        # STATUS → booking eklenmez
def test_cancel_logs_event_with_booking_id()           # booking_events.booking_id FK
def test_update_logs_delta_with_booking_id()           # delta booking_events'te
def test_multi_booking_email()                         # EMAIL-017 → 2 booking satırı
def test_update_synthetic()                            # Sentetik: create → update (Düzeltme #10)
def test_cancel_synthetic()                            # Sentetik: create → cancel (Düzeltme #10)
```

**`test_pipeline_e2e.py`**
```python
def test_golden_set()          # 34 e-postanın intent'i expected_outcomes ile eşleşmeli
def test_malformed_continues() # Hatalı e-posta diğerlerini durdurmamalı
```

---

## Bağımlılıklar (`pyproject.toml`)

```toml
[project]
name = "ugr-booking"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "ollama>=0.4.0",
    "pydantic>=2.0",
    "sqlalchemy>=2.0",
    "streamlit>=1.40",
    "python-dotenv>=1.0",
    "plotly>=5.0",
]

[project.optional-dependencies]
postgres = [
    "psycopg2-binary>=2.9",
]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]
```

---

## `.env.example`

```env
# SQLite (varsayılan — kurulum gerektirmez)
DATABASE_URL=sqlite:///./ugr_booking.db

# PostgreSQL (isteğe bağlı)
# DATABASE_URL=postgresql://user:password@localhost:5432/ugr_booking

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
DATA_PATH=./data
LOG_LEVEL=INFO
```

---

## `.gitignore` Eklemeleri

```
data/
.env
*.db
__pycache__/
.venv/
```

---

## 3 Günlük Zaman Planı (Düzeltilmiş)

### Gün 1 — Çekirdek Pipeline

| # | İş | Süre |
|---|---|---|
| 0 | `ollama pull qwen2.5:7b`, pyproject.toml, .env, DB init | 30 dk |
| 1 | ORM modelleri (4 tablo, FK'lar) + connection.py + repository.py | 60 dk |
| 2 | master_data.py + domain_map.json (müşteri, liman, domain eşleme) | 25 dk |
| 3 | preprocessor.py (encoding uyarı, toleranslı quote regex, hash, attachment) | 70 dk |
| 4 | llm_parser.py (format=JSON, temp=0, proposed_action, multi-booking) | 90 dk |
| 5 | conftest.py Fake LLM + test_preprocessor.py | 50 dk |
| ⚡ | **Gün 1 sonu: 5–6 e-posta gerçek Ollama ile dene, prompt ayarla** | 30 dk |

### Gün 2 — Doğrulama, Agent, Testler + Dashboard İskeleti

| # | İş | Süre |
|---|---|---|
| 6 | validator.py (sayı/tarih/birim, VIN, alias lookup, kanıt, injection, tuzaklar) | 100 dk |
| 7 | agent.py (guard + guard log + dispatch + sentetik tool'lar) | 80 dk |
| 8 | scripts/run_pipeline.py (manifest → tüm e-postalar) | 30 dk |
| 9 | test_validator.py + test_agent.py (tüm tuzak + sentetik fixture) | 80 dk |
| 10 | Pipeline'ı 34 e-posta ile çalıştır, hataları gider | 60 dk |
| 11 | **Dashboard iskeleti** (KPI + tablo + filtreler + İnceleme Kuyruğu) | 60 dk |

### Gün 3 — Chatbot, Golden Set, README, Cila

| # | İş | Süre |
|---|---|---|
| 12 | Chatbot (sorgu fonksiyonları + şablon yanıtlar + Streamlit) | 75 dk |
| 13 | Dashboard grafikleri + chatbot entegrasyon (filtre senkronu) | 45 dk |
| 14 | test_pipeline_e2e.py golden set (34 e-posta) | 60 dk |
| 15 | README (kurulum, çalıştırma, mimari, kısıtlamalar, yetim notu) | 45 dk |
| 16 | Son gözden geçirme, temizlik, GitHub push | 30 dk |

**Toplam:** ~14.5 saat

---

## Tasarım Kararları (v3)

| Karar | Gerekçe |
|---|---|
| SQLite varsayılan | Reviewer tek `uv sync --extra dev` + `python run_pipeline.py` ile başlatır |
| LLM'e müşteri/liman listesi verme | Model alias çözümlemesi yaparsa test edilemez, sessiz hata olur |
| `format=JSON` + `temperature=0` | Non-determinizmi azalt, retry ihtiyacını düşür |
| Niyet LLM sınıflandırır + `proposed_action` → tool Python çağırır | Guard her kararı loglar; sunum "AI-tool kontrolü" için net kanıt |
| Chatbot sayıları şablonla üretir, LLM serbest metinle sınırlı | Yanlış sayı riski sıfır; dashboard tutarlılığı garantili |
| `source_emails` ayrı tablo | NON_BOOKING + STATUS'u booking sayısına katmamak için |
| `booking_events.booking_id` FK | Ref'siz booking'lerde de olay kaydedilebilir |
| `validation_flags` `{field, code, raw, suggestion}` nesneleri | Dashboard detaylı "neden?" gösterimi |
| Fuzzy match yok | Sessiz yanlış eşleşme > açık doğrulama hatası |
| Aksan normalize güvenli, kayıp karakter değil | SÃGUNTO → ESSAG; JEDD?H → VALIDATION_REQUIRED |
| Kanıt kontrolü (LLM çıktısı body'de geçiyor mu?) | Hallüsinasyon + prompt injection ikisini birden yakalar |
| Semantik kopya fingerprint (Aşama 4) | Hash kopya EMAIL-008/012'yi yakalamaz, fingerprint yakalıyor |
| Fake LLM fixture | Testler hızlı, deterministik, CI'da çalışır |
| `received_at` sırasına göre işleme | Güncelleme/iptaller doğru sıraya bağlı |
| `domain_map.json` ayrı config | Master data'da domain bilgisi yok; varsayım açıkça belgelenir |
| Dashboard'da İnceleme Kuyruğu | Yetim/şüpheli e-postalar `bookings`'e girmez, kaybolmaz |
| Güncelleme/iptal sentetik birim testi | Veri setinde 6 yetim var; UPDATE/cancel kodu test edilmiş olur |
