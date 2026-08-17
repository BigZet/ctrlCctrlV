# Вернём CTRL+C / CTRL+V при работе с VDI на macOS

При работе через VDI общий буфер обмена между локальным Mac и удалённым рабочим столом может быть отключён.

Этот проект добавляет два сценария:

```text
VDI → Mac
выделить область экрана → OCR → локальный clipboard

Mac → VDI
локальный clipboard → эмуляция клавиатурного ввода → VDI
```

Основные горячие клавиши:

| Комбинация                | Действие                                                                  |
| ------------------------- | ------------------------------------------------------------------------- |
| `Ctrl + Option + Cmd + 4` | Выделить область → OCR → сохранить текст в clipboard Mac                  |
| `Ctrl + Option + Cmd + V` | Напечатать содержимое clipboard в активном окне через эмуляцию клавиатуры |
| `Ctrl + Option + Cmd + R` | Перезагрузить конфигурацию Hammerspoon                                    |

---

# 1. Что используется

Для работы нужны:

* **Hammerspoon** — горячие клавиши, screenshot, clipboard и эмуляция клавиатуры;
* **Apple Command Line Tools / Swift** — сборка OCR;
* встроенный в macOS **Vision Framework**;
* `VNRecognizeTextRequest` — системный OCR Apple.

---

# 2. Клонирование репозитория

```bash
git clone https://github.com/alexeyk222/ctrlCctrlV.git ~/ctrlCctrlV
cd ~/ctrlCctrlV
```

Структура проекта:

```text
ctrlCctrlV/
├── init.lua
├── scripts/
│   └── ocr.swift
└── README.md
```

Назначение файлов:

* `init.lua` — конфигурация Hammerspoon;
* `scripts/ocr.swift` — OCR на Apple Vision.

---

# 3. Установка Hammerspoon

Если установлен Homebrew:

```bash
brew install --cask hammerspoon
```

После установки запустить:

```text
Applications → Hammerspoon
```

В верхней панели macOS появится значок Hammerspoon.

---

# 4. Разрешения macOS

Hammerspoon необходимо дать системные разрешения.

## Accessibility

Открыть:

```text
System Settings
→ Privacy & Security
→ Accessibility
```

Включить:

```text
Hammerspoon
```

Это необходимо для эмуляции клавиатурного ввода.

## Screen Recording

Открыть:

```text
System Settings
→ Privacy & Security
→ Screen & System Audio Recording
```

Включить:

```text
Hammerspoon
```

Это необходимо для захвата области экрана.

После изменения разрешений желательно полностью перезапустить Hammerspoon.

---

# 5. Установка Command Line Tools

Проверить наличие:

```bash
xcode-select --print-path
```

Обычно должно быть:

```text
/Library/Developer/CommandLineTools
```

Проверить Swift:

```bash
xcrun swiftc --version
```

Если Command Line Tools не установлены:

```bash
xcode-select --install
```

---

# 6. Проверка Swift

Перед сборкой OCR рекомендуется проверить, что Swift и macOS SDK совместимы.

```bash
cat >/tmp/test.swift <<'EOF'
import Foundation
print("Foundation OK")
EOF

xcrun swiftc /tmp/test.swift -o /tmp/test && /tmp/test
```

Ожидаемый результат:

```text
Foundation OK
```

Если появляется ошибка:

```text
this SDK is not supported by the compiler
Please select a toolchain which matches the SDK
```

значит версии Swift compiler и macOS SDK несовместимы.

На тестовом Mac с:

```text
macOS 15.1
```

проблема возникала с:

```text
Command Line Tools 16.2
```

и была устранена установкой:

```text
Command Line Tools for Xcode 16.1
```

Скачать нужную версию можно на Apple Developer Downloads:

```text
https://developer.apple.com/download/all/?q=Command%20Line%20Tools%2016.1
```

После установки повторите тест `Foundation OK`.

---

# 7. Установка файлов проекта

Создать рабочий каталог Hammerspoon:

```bash
mkdir -p ~/.hammerspoon/scripts
```

Скопировать конфигурацию:

```bash
cp ~/ctrlCctrlV/init.lua \
   ~/.hammerspoon/init.lua
```

Скопировать OCR source:

```bash
cp ~/ctrlCctrlV/scripts/ocr.swift \
   ~/.hammerspoon/scripts/ocr.swift
```

---

# 8. Сборка OCR

Создать отдельный Swift module cache:

```bash
rm -rf ~/.cache/swift-ocr
mkdir -p ~/.cache/swift-ocr
```

Собрать OCR:

```bash
xcrun swiftc \
  -module-cache-path ~/.cache/swift-ocr \
  -framework Vision \
  ~/.hammerspoon/scripts/ocr.swift \
  -o ~/.hammerspoon/scripts/ocr
```

Проверить, что файл появился:

```bash
ls -lh ~/.hammerspoon/scripts/ocr
```

Проверить запуск:

```bash
~/.hammerspoon/scripts/ocr
```

Ожидаемый результат:

```text
Usage: ocr <image>
```

---

# 9. Загрузка конфигурации Hammerspoon

Через меню Hammerspoon:

```text
Hammerspoon
→ Reload Config
```

При успешной загрузке появится:

```text
VDI Tools loaded ✓
```

В дальнейшем конфигурацию можно перезагружать комбинацией:

```text
Ctrl + Option + Cmd + R
```

---

# 10. Использование

## VDI → Mac

Нажать:

```text
Ctrl + Option + Cmd + 4
```

Далее:

1. выделить область экрана с текстом или кодом;
2. дождаться сообщения:

```text
OCR → Clipboard ✓
```

3. вставить результат на Mac обычным:

```text
Cmd + V
```

Схема:

```text
VDI
 ↓
Screenshot
 ↓
Apple Vision OCR
 ↓
Mac clipboard
```

---

## Mac → VDI

Скопировать текст на Mac обычным способом:

```text
Cmd + C
```

Поставить курсор в нужное место внутри VDI и нажать:

```text
Ctrl + Option + Cmd + V
```

Hammerspoon не использует VDI clipboard — он последовательно генерирует события клавиатуры и «печатает» текст в активное окно.


---

# 11. Обновление проекта

После изменения `init.lua`:

```bash
cp ~/ctrlCctrlV/init.lua \
   ~/.hammerspoon/init.lua
```

После этого:

```text
Ctrl + Option + Cmd + R
```

После изменения `scripts/ocr.swift`:

```bash
cp ~/ctrlCctrlV/scripts/ocr.swift \
   ~/.hammerspoon/scripts/ocr.swift
```

и пересобрать OCR:

```bash
xcrun swiftc \
  -module-cache-path ~/.cache/swift-ocr \
  -framework Vision \
  ~/.hammerspoon/scripts/ocr.swift \
  -o ~/.hammerspoon/scripts/ocr
```

Reload Hammerspoon после изменения только `ocr.swift` не требуется.

---

# 12. Быстрая диагностика

## Hotkey не работает

Проверить:

```text
System Settings
→ Privacy & Security
→ Accessibility
→ Hammerspoon = ON
```

---

## Не появляется выбор области

Проверить:

```text
System Settings
→ Privacy & Security
→ Screen & System Audio Recording
→ Hammerspoon = ON
```

---

## `OCR binary not found`

Проверить:

```bash
ls -lh ~/.hammerspoon/scripts/ocr
```

Если файла нет — повторить сборку из раздела **«Сборка OCR»**.

---

## Ошибка Swift / SDK

Проверить:

```bash
xcrun swiftc --version
xcrun --show-sdk-path
```

И выполнить тест:

```bash
xcrun swiftc /tmp/test.swift -o /tmp/test && /tmp/test
```

Если появляется:

```text
this SDK is not supported by the compiler
```

необходимо установить совместимую версию Command Line Tools.

---

# 13. Ограничения

OCR хорошо подходит для переноса текста и большинства фрагментов кода, однако распознавание исходного кода не гарантирует 100% точность.

Особенно внимательно стоит проверять:

```text
,
.
:
;
()
[]
{}
_
|
```

Мелкая пунктуация и отдельные строки вроде:

```text
)
```

иногда могут быть пропущены OCR.

Перед выполнением перенесённого кода рекомендуется быстро проверить результат.
