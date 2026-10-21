# Sunum Notları (5-10 dk)

Case'in sorduğu sekiz soruya sırayla cevap. Sayılar `docs/eval_results.md` ve canlı DB'den alınmıştır.

## 1. Nereden başladın, neyi önceliklendirdin?
- Önce **dikey dilim**: tek bir e-posta (008) uçtan uca okuma → LLM → DB satırı. Ollama/DB sorunları ilk gün görünsün diye.
- Sonra **güvenlik**: doğrulama kuralları ve guard. Case'in "emin değilsen tahmin etme" kuralı en yüksek öncelikti.
- Dashboard ve chatbot, doğrulama sağlam olduktan sonra geldi.

## 2. Mimari ve neden?
- **LLM okur ve önerir, Python karar verir.** Model sadece yapılandırılmış JSON üretir; müşteri/liman çözümleme, tarih/sayı normalizasyonu ve durum kararı deterministik Python kodunda.
- Model veritabanına yazamaz: her `create/update/cancel` guard'dan geçer, her karar `processing_log`'a düşer.
- Dashboard ve chatbot **aynı sorgu katmanını** (`db/repository.py`) çağırır, bu yüzden sayılar tutarlıdır.
- Chatbot'ta text-to-SQL yok: LLM yalnızca fonksiyon adı + parametre seçer, sayısal cevaplar şablonla üretilir.

## 3. Ne tamamlandı?
- 34 e-posta hatasız işleniyor; bozuk e-posta döngüyü durdurmuyor.
- Doğrulama: müşteri/liman çözümleme, tarih/sayı normalizasyonu, VIN kuralları, kopya tespiti, enjeksiyon örüntüsü.
- Sonuç: **26 booking → 9 READY, 16 VALIDATION_REQUIRED, 1 CANCELLED.**
- Dashboard: KPI, filtreler, tablo + doğrulama bayrağı detayı, kaynak e-posta izlenebilirliği, İnceleme Kuyruğu, filtreye duyarlı grafikler.
- Chatbot: case'in 4 sorusu, kayıtta olmayan müşteri için sabit yanıt, kural tabanlı yedek, dashboard filtre senkronu.

## 4. Ne eksik ve neden?
- **Yetim güncelleme/iptal** (003, 007, 023, 030): bağlanacak kayıt yok, yeni kayıt uydurmadım; İnceleme Kuyruğu'nda duruyor.
- **Ekler okunmuyor** (029): yalnızca işaretleniyor.
- **014/015:** 7B model iki düzeltme e-postasını yeni talep sandı. İkisi VALIDATION_REQUIRED'a düştü, READY olmadı. Bilerek koda dokunmadım; sapma README'de belgelendi.
- Golden set ile tam eşleşme 14/34. Sapmaların çoğu uyarı bayraklarında ve model kaynaklı alan farklarında.

## 5. AI araçları nerede yardım etti?
- TODO (siz doldurun): hangi adımlarda AI kodlama aracı kullandınız. Not alma yeri: `docs/ai_usage.md`.

## 6. AI çıktısını nasıl doğruladın?
- **Golden set** (34 e-posta, elle hazırlanmış beklenen sonuçlar) ve `scripts/eval_real_llm.py` ile fark raporu.
- Her sapma için karar: model mi yanlış, golden set mi, kural mı? (Golden set düzeltmeleri commit mesajında gerekçelendi.)
- **Güvenlik özelliği ölçüldü:** READY kümesi beklenen 9 kayıtla birebir aynı, fazla/eksik yok.
- Otomatik testler (176 test): kurallar, hata yolları, guard (enjeksiyona uyan sahte LLM bile READY üretemez), chatbot, uçtan uca.

## 7. Üretimden önce neyi değiştirirdin?
PostgreSQL + Alembic, insan onay ekranı (`NEEDS_REVIEW` ve `VALIDATION_REQUIRED` için), gerçek posta kutusu, daha güçlü model, yapılandırılmış log ve metrikler.

## 8. Bir gün daha olsaydı ilk ne yapardım?
- Yetim güncellemeleri kural tabanlı eşleştirme ile kaynak kayda bağlamak ve 014/015 türü düzeltmeleri doğru sınıflandırmak (örn. daha güçlü model ya da few-shot).
- Uyarı bayraklarındaki golden sapmalarını tek tek kapatmak.

---

## Demo akışı (rehearsal)
1. `uv run streamlit run app.py`
2. KPI'lar: toplam 26, READY 9, VALIDATION_REQUIRED 16, CANCELLED 1.
3. Sol panelde müşteri/durum filtresi → tablo ve grafikler değişir.
4. Bir VALIDATION_REQUIRED kaydı seç → bayraklar ve kaynak e-posta.
5. İnceleme Kuyruğu → yetim ve kopya e-postalar.
6. Chatbot: "ABC Shipping için kaç rezervasyon var?", "Izmir çıkışlı rezervasyonları göster", "Hangi rezervasyonlar doğrulama bekliyor?", "Son 5 rezervasyonu listele", sonra bilinmeyen bir müşteri (sabit yanıt).
7. Chatbot yanıtındaki "Dashboard Filtrelerini Güncelle" düğmesiyle filtre senkronu.
