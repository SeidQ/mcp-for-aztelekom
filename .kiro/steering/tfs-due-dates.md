---
inclusion: always
---

# TFS defect due dates

Due date iki yerdə ola bilər: TFS sahəsi `Microsoft.VSTS.Scheduling.DueDate` və bug-a yazılmış **comment**. Due date soruşulanda və ya hesabat hazırlananda **hər ikisini** yoxla. `Target Resolve Date`, `Target Date`, `Finish Date` və `Start Date` istifadə olunmur.

## Scope (açıq PROD defektləri)

- Project: `905. AZTelekom OSS and BSS Support`
- Work item types: `TFS_BUG_TYPES` (default `Bug,Aztelekom Bug`). Yalnız `'Bug'` ilə filterləmə.
- `System.State IN ('Active','Proposed')`
- `Microsoft.VSTS.CMMI.FoundInEnvironment = 'PROD'`

## 1. Field-dən oxuma (əsas mənbə)

1. `get_work_item(id, full=True)` və ya WIQL ilə `Microsoft.VSTS.Scheduling.DueDate` oxu.
2. Dəyər UTC saxlanılır (məs. `2026-11-26T23:00:00Z`). Bakı vaxtına (UTC+4) çevir: bu nümunədə **27.11.2026**.
3. WIQL ilə sürətli ayırma: `DueDate >= 'bu gün'` aktual, `DueDate = ''` boşdur. Boş sahə WIQL-də `< 'tarix'` şərtinə də düşür, ona görə boşları `= ''` ilə ayrıca ayır.
4. Field dolu və bu gündən əvvəldirsə, bug **overdue** sayılır.

## 2. Comment-dən çıxarma

1. Hər bug üçün comment-ləri götür: `list_comments(item_id)`. HTML tag-ləri və `&nbsp;` təmizlə.
2. Yalnız tərkibində `due date` olan comment-lər nəzərə alınır (case-insensitive). Məsələn, "missed the deadline" və ya "due to" sayılmır.
3. Comment-də `due date` sözündən sonra gələn **ilk** tarix götürülür. Formatlar:
   - `dd.mm.yyyy`, məs. `Due date: 28.10.2026`
   - `dd.mm.yy`, məs. `Due date is 22.10.26` → 2026
   - `dd.mm`, məs. `Due Date is 13.10`. İl yoxdursa, comment-in yaradıldığı il götürülür.
   - "...the due date has been extended to 29.10.2026" → 29.10.2026
   - "Due date: 31.10.2026 ... This replaces the previously given due date 13.10.2026" → 31.10.2026, çünki ilk tarix götürülür.
4. Bir bug-da bir neçə due date comment-i varsa, **ən son yaradılmış** comment qüvvədədir.

## Field və comment uyğunsuzluğu

Field və comment tarixi fərqli ola bilər (məs. 329502: comment 13.11.2026, field 04.12.2026). Qaydalar:

- Cavabda **field** tarixini əsas göstər, comment tarixini ayrıca sütunda ver.
- Field boşdursa, comment tarixini göstər və mənbənin comment olduğunu qeyd et.
- Overdue qərarını field-ə görə ver. Field boşdursa comment-ə görə ver.
- Field ilə comment fərqlidirsə, bunu açıq qeyd et.

## Hesabat formatı

> Of the N open PROD defects (A Active, P Proposed), D have a due date (F in the field, C only in a comment), and O of those are overdue.

Siyahıda hər bug üçün bunlar göstərilir: ID, field due date (Bakı vaxtı), comment due date, mənbə, overdue flag, comment tarixi və müəllif.
