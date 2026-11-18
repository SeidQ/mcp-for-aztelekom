---
inclusion: auto
name: bug-reproduce-profile
description: TFS bug-ı reproduce və ya retest edərkən portal profilinin seçilməsi (profil yazılmayıbsa CRM standard).
---

# Bug reproduce / retest: profil seçimi

1. Bug-ı reproduce və ya retest edəndə profili əvvəl bug-ın özündən götür: başlıq, Symptom (Preconditions, Steps to Reproduce, Test Data) və comment-lər.
2. Bug-da və ya comment-lərdə profil **yazılmayıbsa**, default **`CRM standard`** ilə et. `Administrator` və ya başqa geniş icazəli profili öz fikrinlə seçmə.
3. Bug-da profil yazılıbsa (məs. "Administrator profile", "Work Order Executor"), məhz həmin profillə et.
4. `CRM standard` ilə lazım olan menyu və ya düymə görünmürsə, dərhal başqa profilə keçmə. Əvvəl istifadəçiyə bildir ki, bu profildə həmin hissə yoxdur və hansı profili istifadə etməyi soruş.
5. Nəticə hesabatında və TFS comment-də istifadə olunan profili göstər (məs. "Retested on PREPROD, CRM standard profile").
6. Hər profil üçün icazələr fərqli ola bilər. Başqa profillə alınan nəticə, bug-dakı profillə alınan nəticənin əvəzi deyil. Fərqli profillə yoxlama aparılıbsa, bunu açıq yaz və bug-ı bağlama.
