# Как собрать

Сборка проверялась только на Windows 11 (PowerShell 5.1).

## Что поставить

- Visual Studio Build Tools 2022 (C++), путь по умолчанию
- Vulkan SDK в `C:\VulkanSDK\<версия>`
- JDK 25 (Eclipse Adoptium; путь задан в `build-caustica.ps1`, при другой версии поправь `JAVA_HOME` там)
- Slang 2026.14.1 → `sdk\slang`, NVIDIA DLSS SDK v310.7.0 → `sdk\DLSS`
- Git

## Шаги

```powershell
git clone https://github.com/PEQHUB/Caustica caustica-src
cd caustica-src
git checkout 54e91c20
git apply ..\caustica-bedrock-look.patch
cd ..
.\build-caustica.ps1 all -NoSharc
```

Jar появится в `caustica-src\build\libs\`. `-NoSharc` собирает без радиансного кэша NVIDIA SHaRC (так собраны релизы).

## Пак текстур

Конвертер берёт пак Vanilla RTX (Bedrock) и делает пак LabPBR для Java. Нужны Python 3, numpy и Pillow, а также jar Minecraft 26.2:

```powershell
cd converter
python convert.py "<папка или .zip Vanilla RTX>" -o ..\converted\VanillaRTX-LabPBR.zip --jar "<путь к 26.2.jar>"
```

Vanilla RTX: https://github.com/Vanilla-RTX/Vanilla-RTX (автор Cubeir, своя лицензия — положи её `LICENSE.txt` в пак).
