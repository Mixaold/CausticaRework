# CausticaRework — Bedrock RTX на Minecraft Java

Трассировка лучей в Minecraft Java 26.2 с картинкой «как в Bedrock RTX»: мягкие тени, туман и дымка, вода
с каустиками, синяя ночь с луной, тёмный Ад с оранжевым дымом. Это доработка открытого мода
[Caustica](https://github.com/ComfyFluffy/Caustica) (path tracing + NVIDIA DLSS) и PBR-текстур
[Vanilla RTX](https://github.com/Vanilla-RTX/Vanilla-RTX), переведённых в формат Java (LabPBR).

> **Бета.** Сделано одним человеком для себя, проверено на одном ПК (RTX 5060 8 ГБ, 1920×1080).
> Будут баги. Проект не связан с Mojang, Microsoft, NVIDIA и авторами Vanilla RTX.

<!-- Скриншоты: положить 3–4 свежих кадра в docs/img/ и раскомментировать.
![День](docs/img/day.jpg) ![Закат](docs/img/sunset.jpg) ![Ночь](docs/img/night.jpg) ![Ад](docs/img/nether.jpg)
-->

[English below](#english)

## Что нужно

| | |
|---|---|
| Игра | Minecraft **Java 26.2** |
| Загрузчик | [Fabric Loader](https://fabricmc.net/use/installer/) **0.19.3+** и [Fabric API](https://modrinth.com/mod/fabric-api) |
| Java | **25** (официальный лаунчер ставит сам) |
| Видеокарта | **NVIDIA GeForce RTX 5060 8GB** со свежим драйвером. Нужна аппаратная трассировка лучей в Vulkan; DLSS — только на RTX. На AMD/Intel не проверялось |
| Система | Windows 10/11 64-бит |
| Память | выделить игре 6 ГБ (`-Xmx6G`) |

Сколько FPS: у автора на RTX 5060, 1080p, DLSS «Качество», прорисовка 8 чанков — около 70 стоя и 63 в полёте
(замер 21.10.2026 на тестовом стенде с модпаком). На слабее карте будет меньше.

## Как поставить

1. Поставь Fabric Loader для 26.2 и положи Fabric API в папку `mods`.
2. Скачай со страницы [Releases](../../releases) два файла: мод `caustica-rework-*.jar` и пак `VanillaRTX-LabPBR-*.zip`.
3. `caustica-rework-*.jar` → в `.minecraft\mods\`. Пак → в `.minecraft\resourcepacks\` и включи его в игре
   (Настройки → Пакеты ресурсов).
4. В настройках графики выбери движок **Vulkan** (в `options.txt` это строка `preferredGraphicsBackend:"vulkan"`).
   Без Vulkan мод не работает.
5. В аргументы JVM профиля лаунчера добавь `-Xmx6G -Xss16m` (большой стек главного потока советует форк Caustica).
6. Зайди в мир. Первый запуск дольше обычного: собираются шейдеры.

Настройки мода — в игре: Настройки → Графика (раздел Caustica), или файл `.minecraft\config\caustica.toml`.
Ничего переключать между Верхним миром и Адом не нужно.

## С чем не дружит

Мод заменяет отрисовку мира целиком, поэтому **убери** моды, которые тоже её меняют:
Sodium (и Sodium Extra, Reese's Sodium Options), Iris и шейдеры, Distant Horizons, ImmediatelyFast,
Better Block Entities. Моды интерфейса, звука, геймплея обычно работают.

## Известные проблемы

- Против низкого солнца местами остаётся лёгкое серое затемнение.
- Вода в болотах темнее, чем в Bedrock.
- В Аду экспозиция подстраивается не идеально.
- Генерация кадров (Frame Generation) экспериментальная, нужна RTX 40/50.
- Если после краша игра открылась на OpenGL — снова включи Vulkan в настройках графики.

Нашёл баг — заведи [Issue](../../issues) со скриншотом, координатами и списком модов.

## Что изменено относительно Caustica

Туман-стена у края прогрузки и дымка как в Bedrock; экспозиция и цвет под Bedrock (день, закат, синяя ночь,
мягкая луна); вода с поглощением по глубине и текущими каустиками; облака-коробки с тенями; мягкие тени солнца;
дождь и намокание; молния; руки в трассировке; тёмный Ад с самосветящимся дымом; трава пропадает вдали как в Bedrock.
Плюс свой конвертер паков Bedrock RTX → Java LabPBR (`converter/`).

Исходники: правки лежат патчем `caustica-bedrock-look.patch` поверх коммита `54e91c20` форка
[PEQHUB/Caustica](https://github.com/PEQHUB/Caustica). Как собрать — [BUILDING.md](BUILDING.md).

## Авторы и лицензии

- **Caustica** — [ComfyFluffy/Caustica](https://github.com/ComfyFluffy/Caustica) и форк [PEQHUB/Caustica](https://github.com/PEQHUB/Caustica),
  лицензия **LGPL-3.0-or-later**. Наши правки кода — под той же лицензией ([COPYING.LESSER](COPYING.LESSER), [COPYING](COPYING)).
- **Vanilla RTX** — автор Cubeir, [github.com/Vanilla-RTX/Vanilla-RTX](https://github.com/Vanilla-RTX/Vanilla-RTX).
  Пак — переделка Vanilla RTX под Java, распространяется бесплатно по
  [лицензии Vanilla RTX](https://github.com/Vanilla-RTX/Vanilla-RTX/blob/master/LICENSE.txt) (файл `LICENSE.txt` лежит в паке).
- **NVIDIA DLSS / Streamline** — внутри jar, по лицензии NVIDIA RTX SDK.
- Minecraft — товарный знак Mojang/Microsoft. Это неофициальный мод.
- Большая часть кода и текстов написана с помощью ИИ (Claude) по заданиям автора.

---

## English

**CausticaRework** brings a Bedrock RTX look to Minecraft Java 26.2: a modified
[Caustica](https://github.com/ComfyFluffy/Caustica) path tracer (DLSS) plus [Vanilla RTX](https://github.com/Vanilla-RTX/Vanilla-RTX)
PBR textures converted to LabPBR. Beta, tested on one PC (RTX 5060 8 GB).

**Needs:** Minecraft Java 26.2, Fabric Loader 0.19.3+, Fabric API, Java 25, an NVIDIA GeForce RTX GPU (Vulkan ray tracing),
Windows 10/11 64-bit.

**Install:** put the mod jar from [Releases](../../releases) into `mods`, the resource pack into `resourcepacks` and enable it,
switch the graphics backend to **Vulkan**, add `-Xmx6G -Xss16m` to JVM arguments. Remove Sodium, Iris, Distant Horizons,
ImmediatelyFast and other world-rendering mods.

**Licenses:** code changes LGPL-3.0-or-later (as Caustica); the resource pack is a derivative of Vanilla RTX by Cubeir, free,
under the [Vanilla RTX license](https://github.com/Vanilla-RTX/Vanilla-RTX/blob/master/LICENSE.txt). Not affiliated with
Mojang, Microsoft, NVIDIA or Vanilla RTX. Made with AI assistance (Claude).
