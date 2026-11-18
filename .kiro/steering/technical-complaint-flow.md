---
inclusion: auto
name: technical-complaint-flow
description: PREPROD-da Technical Complaint (Trouble Ticket / Texniki Şikayət) ticket yaratmaq, TC-01..TC-09 task-larını Administrator ilə bağlamaq, nəticəni UAT Excel-ə yazmaq.
---

# Technical Complaint (Trouble Ticket) – PREPROD axını (04-05.11.2026)

Xam addımlar: `artifacts/recipes/TT_TECH_FIN_E2E_ADMIN.json` (`recipe_show`). Aşağıdakı qısa yol daha sürətlidir.

## Fayllar
- Sənəd: `C:\Users\sgaziyev\Downloads\Technical Complaint.docx` (bölmələr: 2.1.2 CRM request, 3.1.2.1.2 Selection of subscriber, 3.1.2.2.4 Verify financial issue, 3.1.2.2.9 Verify technical issue).
- Excel: `C:\Users\sgaziyev\Desktop\Test cases Aztelekom\UAT_Technical_Complaint_v4.xlsx` (UAT_Cases: A Summary, B Steps, C Expected, D Status, E Comment). Sətirləri **başlığa görə** tap (silinmələr nömrələri dəyişib). `.bak` saxlama.
- Test müştəri: MƏMMƏDLİ DÜRDANƏ, Customer ID 20260200001134, Contact 502471291.
- İstifadə olunmuş abonentlər (açıq ticket var, "same data" xətası verə bilər): 2379, 2610, 2902, 2930. Boş qalanlar: 2954, 2962 və s.

## 1. Ticket yaratmaq (profil CRM)
- `search_customer(customer_id=...)` → Available processes → `click_text('Texniki Şikayət', exact=True, frame=1, nth=<SONUNCU>)`. Requests list-də də "Texniki Şikayət" sətirləri var, ona görə Available processes-dəki link **sonuncu** uyğunluqdur (nth hər yeni ticket ilə artır). `nth=0` köhnə request-i açır.
- Request səhifəsindən müştəriyə qayıtmaq: menyuda "Customer home page" yoxdur → yuxarıdakı müştəri adına `click_text('MƏMMƏDLİ DÜRDANƏ', frame=1)`. Səhifə aşağı sürüşübsə əvvəl `press_key Home frame=1`.
- Step 2: Subscriber lookup = `click Submit frame=1 nth=2` → siyahıdan abonent `click_text`. Ticket Type = `select_option(label='Ticket Type', frame=1)`. `Next`.
- Step 3: Category of service lookup `Submit nth=3` → `GPON`; Subcategory lookup `Submit nth=6` → `Internet yoxdur` / `Sürət zəifliyi`. Ticket source `select_option`. Details `fill_by_row`. `Finish the scenario and start the service process`.
- "There is already a request with the same data" = eyni abonent + kateqoriya ilə açıq ticket var → başqa abonent seç (2379 və 2610 artıq istifadə olunub).
- Proses ID: request-də `Show process details` → `current_state` yeni pəncərənin URL-indəki `processInstance=70000_xxxxx`.

## 2. Task-lar (profil Administrator – rol user-ləri yoxdur, Q-31)
- `open_sunvizion(path='/app/wfl/processes/process-tasks/70000/<id>', profile='Administrator')`. Cədvəl 2 səhifədir: lazım olan sətir görünmürsə `click('next page')` / `click('previous page')`.
- Accept: `sv_select_row` → `sv_toolbar('Accept', confirm=True)` → forma özü açılır.
- **Formadakı Finalize**: klik işləmir. Son sahəyə klik et → `Tab`, `Tab` → screenshot-da Finalize-ın focus-da olduğunu yoxla → `Enter`. Focus Close-dadırsa "unsaved changes" dialoqu çıxır → Cancel.
- **Toolbar Finalize** Technical task-larda +994 Phone Number xətası verir (#332009) – istifadə etmə.
- Kendo tarix (Fix Date): `click_text('Fix Date')` → `Home` → rəqəmlər hərf-hərf `11042026` + `030000` + `P` (portal_batch ilə) → `sv_fields` ilə yoxla.
- Dropdown (Fix to be repeated?, Start internal fix process?): `click(role='combobox')` → `Alt+ArrowDown` → `click_text('Yes'/'No', exact=True)`. `Escape` dəyəri silir.
- Radio (Complaint result, Contact method): `click_text(<dəyər>, exact=True)`.
- Uzun forma: `Toggle Fullscreen Mode`.

## 3. Axınlar (sistemdə yoxlanıb)
- Technical: TC-01 → TC-04 (Fix Date məcburi) → TC-05 (`Yes` → yeni TC-04; `No` → CRM Finalized, **Contact customer yoxdur** – sənəd diaqramı da belədir).
- Financial: TC-06 (Complaint result + Contact method məcburi; Internal **yoxdur**) → TC-09 Contact Customer → Finalized. Routing (05.11): Call center call → Customer Relations Division, Letter / E-mail → Customer Care, Back office call → addressee boş (#330527). TC-09-da yalnız New Comment redaktə olunur.
- TC-06 formasında sv_toolbar('Finalize') formadakı Finalize-ı basır və işləyir (Technical task-lardakı +994 xətası yoxdur).
- Accept-dən sonra "Process Preview" fullscreen-də ilişib qalsa: `open_sunvizion(path=..., new_tab=True)`; forma köhnə pəncərədə açılırsa `current_state` + `switch_page`.
- CRM Result həmişə "Correct Result" (#332049, sənəd: Closed). Task-larda Subscriber həmişə 0050010001787 (#332050).

## 4. SLA / Expires
- Process Tasks və My Tasks-da Expires sütunu yoxdur. `open_sunvizion(path='/app/wfl/tasks/supervised-tasks')` → Task ID sahəsinə `//70000/.<id>` (əvvəl `Clear`) → Search nth=1 → sətrə klik → `End` (sağa sürüşdür) → Expires görünür. Hal-hazırda boşdur (Q-21).

## 4b. OSS Technical issue (05.11, process 70000_902553)
- OSS = Portal → Network Resources Inventory → **Events** tab. "Create new event" → Basic Data (Event Kind: real failure) → General ("Related to resource" Edit → Object axtarışına mətn yaz, məs. `olt` → "Drop Cable to: 4458575") → Description → Create.
- Prosesi başlatmaq: event sətrinə **`right_click`** (yeni alət) → "New Process..." → "Trouble Ticketing - Technical issue" sətrinə `right_click` → "Start Process..." → Next → Start → "Show Tasks".
- Event siyahısı 2 səhifədir; yeni event sonuncu səhifədədir (`next page`). Object Name filtri resurs adı ilə işləmir.
- Task-lar (Administrator): 01 Verify Ticket (Category/Subcategory/Character lookup-ları = `Edit nth=1/2/3`) → 05 Assign Division (Executor lookup) → 06 Work Order (Failure Status: Cancel / Fix required / Issue resolved) → 02b Approve Changing → Close Event (event state = fixed, Repair Time).
- Bu task-larda `sv_toolbar('Finalize', confirm=True)` formadakı Finalize-ı basır və işləyir.

## 5. Bilinən problemlər
- Portal "updated, reload" dialoqu: OK → sessiya bitir → `close_portal` → `open_portal(env='PREPROD', profile='CRM')`.
- `ERR_NAME_NOT_RESOLVED` = VPN qopub.
- CRM boş ağ səhifə: `close_portal` + `open_portal`.
