---
inclusion: always
---

# TFS defect due dates

Due date-lər TFS sahələrində saxlanılmır. `Target Resolve Date`, `Target Date`, `Finish Date` və `Start Date` boşdur, `DueDate` adlı sahə isə yoxdur. Due date bug-a **comment** kimi yazılır. Due date soruşulanda və ya hesabat hazırlananda onu həmişə comment-lərdən çək.

## Scope (açıq PROD defektləri)

- Project: `905. AZTelekom OSS and BSS Support`
- Work item types: `TFS_BUG_TYPES` (default `Bug,Aztelekom Bug`). Yalnız `'Bug'` ilə filterləmə.
- `System.State IN ('Active','Proposed')`
- `Microsoft.VSTS.CMMI.FoundInEnvironment = 'PROD'`

## Comment-dən çıxarma qaydası

1. Hər bug üçün comment-ləri götür: `list_comments(item_id)` və ya `_call("GET", f"wit/workItems/{id}/comments", params={"api-version": COMMENTS_API})`. HTML tag-ləri və `&nbsp;` təmizlə.
2. Yalnız tərkibində `due date` olan comment-lər nəzərə alınır (case-insensitive). Məsələn, "missed the deadline" və ya "due to" sayılmır.
3. Comment-də `due date` sözündən sonra gələn **ilk** tarix götürülür. Formatlar:
   - `dd.mm.yyyy`, məs. `Due date: 28.10.2026`
   - `dd.mm.yy`, məs. `Due date is 22.10.26` → 2026
   - `dd.mm`, məs. `Due Date is 13.10`. İl yoxdursa, comment-in yaradıldığı il götürülür.
   - "...the due date has been extended to 29.10.2026" → 29.10.2026
   - "Due date: 31.10.2026 ... This replaces the previously given due date 13.10.2026" → 31.10.2026, çünki ilk tarix götürülür.
4. Bir bug-da bir neçə due date comment-i varsa, **ən son yaradılmış** comment qüvvədədir.
5. Due date bu gündən əvvəldirsə, bug **overdue** sayılır.

## Hesabat formatı

> Of the N open PROD defects (A Active, P Proposed), D have a due date, and O of those are overdue.

Siyahıda hər bug üçün bunlar göstərilir: ID, due date, overdue flag, comment tarixi və müəllif.
