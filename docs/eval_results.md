# UGR Booking — LLM Evaluation Report

## Sunum Özeti (Final Eval Skoru)
- **Toplam E-posta:** 34
- **Tam Eşleşme (Golden Set Match):** 14 / 34 (%41.2)
- **Doğrulanmış READY Kümesi (0 False Positive):** 9/9 (%100) — Beklenen 9 e-postanın (`EMAIL-002`, `009`, `016`, `021`, `022`, `027`, `028`, `033`, `034`) tamamı READY olmuş, tek bir geçersiz kayıt dahi yanlışlıkla READY yapılmamıştır (`fazla: []`, `eksik: []`).
- **Bilinen Sapma (014 & 015):** 7B açık kaynaklı model (Qwen 2.5 7B), iki düzeltme e-postasını (`EMAIL-014`, `EMAIL-015`) güncelleme (`UPDATE_BOOKING`) yerine yeni talep (`CREATE_BOOKING`) olarak algılamıştır. Güvenlik katmanı (guard ve validasyon) devreye girerek bu iki kaydın `READY` olmasını engellemiş ve `VALIDATION_REQUIRED` statüsünde güvenli şekilde karantinaya almıştır.

**Total emails evaluated:** 34  
**Matching golden set:** 14 / 34 (41.2%)  
**Deviations found in:** 20 email(s)

## Summary

| Email | Result | Deviations |
|---|---|---|
| EMAIL-001 | ❌ DIFF | split=0 extra_blocking_flags: 'not expected'→['MISSING_REQUIRED']; split=0 missing_warning_flags: ['NUMBER_NORMALIZED']→'not raised'; split=0 extra_warning_flags: 'not expected'→['QUANTITY_FROM_VIN_COUNT'] (+1 more) |
| EMAIL-002 | ❌ DIFF | split=0 extra_warning_flags: 'not expected'→['QUANTITY_FROM_VIN_COUNT'] |
| EMAIL-003 | ✅ MATCH |  |
| EMAIL-004 | ✅ MATCH |  |
| EMAIL-005 | ❌ DIFF | split=0 extra_blocking_flags: 'not expected'→['UNKNOWN_PORT'] |
| EMAIL-006 | ✅ MATCH |  |
| EMAIL-007 | ✅ MATCH |  |
| EMAIL-008 | ✅ MATCH |  |
| EMAIL-009 | ❌ DIFF | split=0 extra_warning_flags: 'not expected'→['NUMBER_NORMALIZED', 'QUANTITY_FROM_VIN_COUNT'] |
| EMAIL-010 | ❌ DIFF | split=0 extra_blocking_flags: 'not expected'→['MISSING_REQUIRED']; split=0 missing_fields: []→['customer_id'] |
| EMAIL-011 | ✅ MATCH |  |
| EMAIL-012 | ✅ MATCH |  |
| EMAIL-013 | ✅ MATCH |  |
| EMAIL-014 | ❌ DIFF | split=-1 intent: 'UPDATE_BOOKING'→'CREATE_BOOKING'; split=-1 source_status: 'ORPHAN'→'BOOKING_CREATED'; split=-1 booking_count: 0→1 |
| EMAIL-015 | ❌ DIFF | split=-1 intent: 'UPDATE_BOOKING'→'CREATE_BOOKING'; split=-1 source_status: 'ORPHAN'→'BOOKING_CREATED'; split=-1 booking_count: 0→1 |
| EMAIL-016 | ❌ DIFF | split=0 extra_warning_flags: 'not expected'→['QUANTITY_FROM_VIN_COUNT'] |
| EMAIL-017 | ❌ DIFF | split=0 missing_blocking_flags: ['AMBIGUOUS_DATE']→'not raised'; split=0 extra_blocking_flags: 'not expected'→['MISSING_REQUIRED']; split=0 extra_warning_flags: 'not expected'→['QUANTITY_FROM_VIN_COUNT'] (+3 more) |
| EMAIL-018 | ❌ DIFF | split=0 extra_blocking_flags: 'not expected'→['UNKNOWN_PORT']; split=0 missing_fields: ['cargo_description', 'pod', 'pol', 'requested_date']→['pod', 'pol', 'requested_date'] |
| EMAIL-019 | ✅ MATCH |  |
| EMAIL-020 | ❌ DIFF | split=0 extra_blocking_flags: 'not expected'→['MISSING_REQUIRED'] |
| EMAIL-021 | ❌ DIFF | split=0 extra_warning_flags: 'not expected'→['QUANTITY_FROM_VIN_COUNT'] |
| EMAIL-022 | ❌ DIFF | split=0 extra_warning_flags: 'not expected'→['QUANTITY_FROM_VIN_COUNT'] |
| EMAIL-023 | ✅ MATCH |  |
| EMAIL-024 | ✅ MATCH |  |
| EMAIL-025 | ❌ DIFF | split=0 extra_blocking_flags: 'not expected'→['UNKNOWN_CUSTOMER']; split=0 missing_warning_flags: ['EMPTY_BODY']→'not raised'; split=0 missing_fields: ['cargo_description', 'customer_id', 'requested_date']→['customer_id', 'requested_date'] |
| EMAIL-026 | ❌ DIFF | split=0 extra_blocking_flags: 'not expected'→['MISSING_REQUIRED']; split=0 extra_warning_flags: 'not expected'→['NUMBER_NORMALIZED', 'QUANTITY_FROM_VIN_COUNT'] |
| EMAIL-027 | ❌ DIFF | split=0 missing_warning_flags: ['NUMBER_NORMALIZED']→'not raised'; split=0 extra_warning_flags: 'not expected'→['QUANTITY_FROM_VIN_COUNT'] |
| EMAIL-028 | ❌ DIFF | split=0 missing_warning_flags: ['CONFLICTING_VALUES']→'not raised' |
| EMAIL-029 | ✅ MATCH |  |
| EMAIL-030 | ✅ MATCH |  |
| EMAIL-031 | ❌ DIFF | split=0 extra_warning_flags: 'not expected'→['QUANTITY_FROM_VIN_COUNT'] |
| EMAIL-032 | ❌ DIFF | split=0 missing_blocking_flags: ['AMBIGUOUS_DATE']→'not raised'; split=0 extra_blocking_flags: 'not expected'→['MISSING_REQUIRED'] |
| EMAIL-033 | ✅ MATCH |  |
| EMAIL-034 | ❌ DIFF | split=0 extra_warning_flags: 'not expected'→['PORT_ACCENT_NORMALIZED'] |

## Deviation Detail

### EMAIL-001

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_blocking_flags` | `not expected` | `['MISSING_REQUIRED']` |
| 0 | `missing_warning_flags` | `['NUMBER_NORMALIZED']` | `not raised` |
| 0 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |
| 0 | `missing_fields` | `['pod', 'requested_date']` | `['requested_date']` |

### EMAIL-002

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-005

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_blocking_flags` | `not expected` | `['UNKNOWN_PORT']` |

### EMAIL-009

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_warning_flags` | `not expected` | `['NUMBER_NORMALIZED', 'QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-010

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_blocking_flags` | `not expected` | `['MISSING_REQUIRED']` |
| 0 | `missing_fields` | `[]` | `['customer_id']` |

### EMAIL-014

| split | field | expected | actual |
|---|---|---|---|
| -1 | `intent` | `UPDATE_BOOKING` | `CREATE_BOOKING` |
| -1 | `source_status` | `ORPHAN` | `BOOKING_CREATED` |
| -1 | `booking_count` | `0` | `1` |

### EMAIL-015

| split | field | expected | actual |
|---|---|---|---|
| -1 | `intent` | `UPDATE_BOOKING` | `CREATE_BOOKING` |
| -1 | `source_status` | `ORPHAN` | `BOOKING_CREATED` |
| -1 | `booking_count` | `0` | `1` |

### EMAIL-016

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-017

| split | field | expected | actual |
|---|---|---|---|
| 0 | `missing_blocking_flags` | `['AMBIGUOUS_DATE']` | `not raised` |
| 0 | `extra_blocking_flags` | `not expected` | `['MISSING_REQUIRED']` |
| 0 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |
| 1 | `missing_blocking_flags` | `['AMBIGUOUS_DATE']` | `not raised` |
| 1 | `extra_blocking_flags` | `not expected` | `['MISSING_REQUIRED']` |
| 1 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-018

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_blocking_flags` | `not expected` | `['UNKNOWN_PORT']` |
| 0 | `missing_fields` | `['cargo_description', 'pod', 'pol', 'requested_date']` | `['pod', 'pol', 'requested_date']` |

### EMAIL-020

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_blocking_flags` | `not expected` | `['MISSING_REQUIRED']` |

### EMAIL-021

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-022

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-025

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_blocking_flags` | `not expected` | `['UNKNOWN_CUSTOMER']` |
| 0 | `missing_warning_flags` | `['EMPTY_BODY']` | `not raised` |
| 0 | `missing_fields` | `['cargo_description', 'customer_id', 'requested_date']` | `['customer_id', 'requested_date']` |

### EMAIL-026

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_blocking_flags` | `not expected` | `['MISSING_REQUIRED']` |
| 0 | `extra_warning_flags` | `not expected` | `['NUMBER_NORMALIZED', 'QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-027

| split | field | expected | actual |
|---|---|---|---|
| 0 | `missing_warning_flags` | `['NUMBER_NORMALIZED']` | `not raised` |
| 0 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-028

| split | field | expected | actual |
|---|---|---|---|
| 0 | `missing_warning_flags` | `['CONFLICTING_VALUES']` | `not raised` |

### EMAIL-031

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_warning_flags` | `not expected` | `['QUANTITY_FROM_VIN_COUNT']` |

### EMAIL-032

| split | field | expected | actual |
|---|---|---|---|
| 0 | `missing_blocking_flags` | `['AMBIGUOUS_DATE']` | `not raised` |
| 0 | `extra_blocking_flags` | `not expected` | `['MISSING_REQUIRED']` |

### EMAIL-034

| split | field | expected | actual |
|---|---|---|---|
| 0 | `extra_warning_flags` | `not expected` | `['PORT_ACCENT_NORMALIZED']` |

## Legend

- **missing_blocking_flags**: golden set expects this flag but validator did not raise it
- **extra_blocking_flags**: validator raised this flag but golden set does not expect it
- **source_status**: SourceEmail.processing_status mismatch
- **intent**: classified_intent mismatch

> Generated by `scripts/eval_real_llm.py`
