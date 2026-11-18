---
inclusion: auto
name: portal-test-execution
description: Portal/SunVizion test case (TC) icrası, UAT addımları, recipe, portal-ui alətləri ilə klik/fill axını sürətləndirmə.
---

# Portal test case icrası

Element yaddaşı (`src/state/element_memory.json`) kodda avtomatik işləyir: frame axtarışı, işləyən klik üsulu, vaxt. Ona görə yaddaşı əl ilə yazma.

**Alət adları (birləşdirilib).** Bu faylda və digər steering fayllarında köhnə adlar keçir. MCP-də birbaşa çağıranda bunlardan istifadə et. `portal_batch` addımlarında (`{"do": ...}`) köhnə adlar OLDUĞU KİMİ işləyir.

| Köhnə ad | Birbaşa çağırış |
|---|---|
| `fill`, `fill_by_row`, `fill_nth`, `type_text` | `fill(by="label"\|"row"\|"nth"\|"type", value, target, index, frame)` |
| `click`, `click_text`, `right_click`, `click_search` | `click(by="button"\|"text"\|"right"\|"search", target, exact, frame, role, nth)` |
| `read_text`, `inspect_page`, `current_state`, `check_layout` | `inspect(mode="text"\|"page"\|"state"\|"layout")` |
| `recipe_start/save/list/show/run`, `learn_report` | `recipe(action="start"\|"save"\|"list"\|"show"\|"run"\|"learn", ...)` |
| `api_capture/log/endpoints/call/run_tests` | `api(action="capture"\|"log"\|"endpoints"\|"call"\|"run_tests", ...)` |
| `sv_grid_read`, `sv_open_row`, `sv_select_row` | `sv_grid(action="read"\|"open"\|"select", ...)` |
| `upload_file`, `list_file_inputs`, `make_test_files` | `upload(action="upload"\|"list_inputs"\|"make_files", ...)` |

1. **Başlamazdan əvvəl:** `recipe_list` çağır. Bu TC üçün recipe varsa, `recipe_show` ilə `note` və "işləməyən cəhdlər"ə bax. Naviqasiyanı `recipe_run` və ya bir `portal_batch` ilə keç, addım-addım ayrıca çağırış etmə.
2. **İcra:** Əvvəlcə `recipe_start('<TC adı>')` çağır. Ardıcıl addımları `portal_batch`-də birləşdir. Nəticəni `expect_text` ilə yoxla, hər addımdan sonra screenshot çəkmə.
3. **Sonda:** `recipe_save(name, note=..., include_failed=True)` çağır. `note`-a 1-3 sətir yaz: hansı element çaşdırdı, düzgün yol nə idi (məs. "Search nth=1 aşağıdakı düymədir"). Uzun izah yazma.
4. **Kod düzəlişi:** Kodu avtomatik dəyişmə. İstifadəçi soruşanda `learn_report` çağır. YAVAŞ, XƏTA və ya FALLBACK olan elementlər üçün `src/portal_mcp_server.py`-də düzəliş təklif et.
5. Cavabda `[fallback: ... (öyrənilmiş)]` görünürsə, yaddaş işləyib. Səhv element seçilibsə, `learn_report(target='...', forget=True)` ilə həmin qeydi sil.

## Sübut (evidence) screenshot-ları

6. TFS-ə və ya Excel-ə gedən hər screenshot **marked** olmalıdır. `python src/mark_shot.py <shot.png> "x1,y1,x2,y2:qısa etiket" ...` skripti `<ad>_marked.png` yaradır. TFS-ə yalnız `_marked` versiyanı göndər.
7. Yalnız nəticəni sübut edən sətir və ya sahələri işarələ: status, xəta mesajı, gözlənilən və faktiki dəyər. Bütün ekranı, menyunu və ya nəticə ilə əlaqəsi olmayan sətirləri işarələmə. Ən çox 3-4 qutu çək, etiketlər qısa olsun.
8. Sübut üçün düzgün ekranı seç: lazım olan sətir görünmürsə, əvvəlcə səhifələmə və ya filtrlə həmin sətri ekrana gətir, sonra çək.
9. Hər defektdə **"Document reference"** bölməsi olsun: bölmə nömrəsi + sənəddən dəqiq sitat + sənədin həmin hissəsinin marked screenshot-u (docx → Word ilə HTML-ə export → Playwright ilə kəsik). Defekt açmazdan əvvəl gözlənilən nəticəni sənəddə (mətn + proses diaqramı) yoxla, Excel-dəki gözləntiyə güvənmə.
10. TFS comment-ləri və History qeydləri `[Said Gaziyev]` ilə başlasın (avtomatik prefiks işləmir).
10a. Defektin başlığı, bütün bölmələri və screenshot başlıqları **ingiliscə** yazılsın (istifadəçi ilə söhbət azərbaycanca olsa belə). Format əvvəlki defektlərlə eyni olsun (məs. #332099): Description içində "Document reference: <docx>, <bölmə>, field table: &quot;...&quot;", Preconditions, Test Data (proses/ticket nömrələri), STR, Expected, Actual, Screenshots, Logs, Related Work. `tags` vermə (TF401289: tag yaratmağa icazə yoxdur). Screenshot yolları tam (absolute) olsun.
10b. **Description / Symptom qısa olsun** — 1-2 cümlə + bir sətir "Document reference: ...". Uzun sənəd sitatlarını və dəyər/sahə siyahılarını Description-a yığma; onları müvafiq bölmələrə köçür: field/dəyər siyahıları Supporting Logs/Trace-ə, tam sitat isə Document reference sətrinə (qısa). STR/Expected/Actual da yığcam olsun.
10c. `create_defect_full`-un `screenshots` arqumentini **list** kimi ver (sətir yox) və caption-larda `:` və ya `=` işlətmə (parse xətası verir). Uzun/tam Symptom HTML-i isə yaratdıqdan sonra `update_fields`-in `extra_fields` ({"Microsoft.VSTS.CMMI.Symptom": "<html>"}) ilə yaz.

## Sürətli icra
11. Hər test axını bitəndə `recipe_save(name, note, include_failed=True)` çağır və axın üçün `.kiro/steering/<axın>-flow.md` faylını yenilə (işləyən yol, nth dəyərləri, tələlər). Növbəti icrada əvvəlcə həmin steering faylını oxu.
