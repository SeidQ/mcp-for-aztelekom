---
inclusion: auto
name: b2c-sale-flow
description: PREPROD-da B2C Sale (Internet / IPTV) yaratmaq və workflow prosesini (FC-04, ödəniş, Device Selection, ANS-01, moving devices) sona qədər aparmaq.
---

# B2C Sale – PREPROD axını (03.11.2026, TR00010599 ilə yoxlanılıb)

Tam addımlar: `artifacts/recipes/B2C_SALE_INT_IPTV_315609.json` (`recipe_show`).

## 1. CRM: satışı yaratmaq (profil CRM)
- Find Subscriber → 2-ci checkbox (`click role=checkbox nth=1`) → ünvan vidceti **frame 3**-dədir.
  - Address Suggestions: `click combobox nth=1` → "Zaq" yaz → `Zaqatala`.
  - City / Street / Building: `click button nth=2 / 6 / 7` → bir neçə hərf yaz → siyahıdan `click_text`.
  - `Search nth=1` (frame 1) → "No subscribers in a given location" → Scenario list → **B2C Sale** (təxminən 25 saniyə yüklənir).
- Use client from context → Lead/Application: `combobox nth=0/1` + hərf + Enter → Next.
- Product Order: `Submit nth=3` (`nth=1` paneli bağlayır) → yeni pəncərə (frame 1): Add offer → Internet + IPTV → Search → offer → Approve → Next → Save.
- Offer Accepted: `combobox` + Y + Enter → Next → `fill_by_row` ilə tarix və alternate nömrə → Finish.

## 2. Workflow-da dayanma yerləri (səbəblər)
| Dayandığı yer | Səbəb | Necə keçmək olar |
|---|---|---|
| `Catch message Product Order Execution` | Ünvan avtomatik feasibility-dən keçmir, **FC-04** manual task açılır | Tasks səhifəsi (profil Technical Design/Pasportization) → Load All → FC-04 sətri (subscriber nömrəsi ilə) → Accept → `Service Verification Task Action` = Accept (`click_text` + Alt+ArrowDown + ArrowDown + Enter) → Save and Close → toolbar Finalize |
| `Receive message - payment received` | Maliyyə hesabının balansı 0 olmalıdır (`PaymentRetrieved`), məsələn 130 AZN | Ödəniş (BSS `/v1/payment`, base URL istifadəçidən). Balansı CRM Subscriber page → Financial accounts bölməsindən yoxla |
| `Contract Printing by User` | Manual task | Accept → Save and Close → toolbar Finalize |

## 3. Cihazlar
- **Device Selection** (CRM profili): Warehouse = `Edit` → `Go to next page nth=1` x3 → Zaqatala TKQ → Select. ONT `Add nth=0`, STB `Add nth=1` → seriya nömrəsi → Select → Save and Close → toolbar **Accept and Finalize**. Formadakı Finalize düyməsi işləmir.
- **ANS-01** (profil **Administrator**): ONT sətri → `Edit nth=0` → Password, WIFI_SSID, WIFI_SSID_2_4, WIFI_SSID_5_0, WIFI_Password sahələrini `click textbox` + hərf-hərf `press_key` ilə doldur (`fill` işləmir) → Save → Save and Close → toolbar Accept and Finalize.
- Bundan sonra avtomatik gələn addımlar: `Parallel task - moving devices`, 2 dəfə `Call activity - move device` / `UpdateDeviceLocation`, Radius, IPTV Connect, Create ONT/STB, sonra **Dispatch installation (FC-02)**.

## Qeydlər
- Task preview açılmırsa: başqa sətrə klik et, sonra yenidən double-click et. Message tab-ı yalnız service task-larda olur.
- Cədvəl köhnə səhifədə qalırsa, prosesi `open_sunvizion(path=..., new_tab=True)` ilə yenidən aç.
