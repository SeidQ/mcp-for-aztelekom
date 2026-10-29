# AZTelekom TFS Defects (Claude Desktop extension)

Quraşdırma: `dist\aztelekom-tfs-1.0.1.mcpb` faylını Claude desktop-da açın (iki dəfə klik və ya Settings → Extensions →
faylı sürükləyin) → Install → Ad Soyad, TFS istifadəçi adı və parolu daxil edin.

- Hamı ortaq AZTelekom TFS hesabı ilə işləyir; Ad Soyad comment-lərə və yeni defekt başlıqlarına avtomatik `[Ad Soyad]` kimi yazılır.
- Silmə alətləri hər dəfə ekranda Yes/No təsdiq pəncərəsi açır (3+ element üçün rəqəm yazmaq lazımdır).
- Lokal məlumatlar: `%USERPROFILE%\.aztelekom-tfs\` (cache, audit log, RCA qərarları).
- Defect monitor (avtomatik mail) bu paketdə YOXDUR — yalnız Said Gaziyevin kompüterində işləyir.
- Python asılılıqları ilk işə düşmədə `uv` ilə avtomatik quraşdırılır (internet lazımdır).
