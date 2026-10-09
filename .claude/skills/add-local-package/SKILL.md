---
name: add-local-package
description: Чек-лист добавления нового локального SwiftPM-пакета в Packages/ и подключения его к приложению через project.xcproj. Используй, когда нужно добавить, создать или выделить новый пакет, модуль или библиотеку (add/create a new local package, module, library) в этом проекте.
---

# Добавление локального SPM-пакета

Чек-лист. Детали формата `project.xcproj` — только в `docs/xcproj-notes.md`, здесь они не повторяются.

## 1. Уточнить у пользователя

Спроси (AskUserQuestion), **должен ли пакет собираться на Linux**. Ответ определяет всё дальнейшее:

| | Linux: да (как `GarageCore`) | Linux: нет (как `GarageStorage`) |
|---|---|---|
| `platforms` | как в `Packages/GarageCore/Package.swift` | как в `Packages/GarageStorage/Package.swift` |
| Импорты | только Foundation; без UIKit/SwiftUI/AppKit/Combine/SwiftData | любые iOS-фреймворки |
| Тесты | `swift test --package-path Packages/<Name>` | `xcodebuild test` на симуляторе из `CLAUDE.local.md` |

Также уточни имя пакета и зависимости от других пакетов, если они не очевидны из запроса.

## 2. Создать пакет

- [ ] `Packages/<Name>/Package.swift` по образцу `Packages/GarageCore/Package.swift` / `Packages/GarageStorage/Package.swift`: `swift-tools-version: 6.0`, один `.library`, зависимости через `.package(path: "../<Other>")`.
- [ ] **Всегда** `.testTarget(name: "<Name>Tests", dependencies: ["<Name>"])`.
- [ ] `Sources/<Name>/` — минимум один файл; типы, нужные приложению, — `public`.
- [ ] `Tests/<Name>Tests/` — минимум один тест на Swift Testing (`import Testing`, `@Test`, `#expect`), не XCTest.
- [ ] Тесты пакета проходят (команда из таблицы выше).

## 3. Подключить к приложению

- [ ] Прочитать `docs/xcproj-notes.md` (раздел про локальный пакет) и внести правки в `Garage.xcodeproj/project.xcproj` строго по нему.
- [ ] `$DEVELOPER_DIR/usr/bin/xcprojformatter --update Garage.xcodeproj` (хук делает это после Edit, но запусти явно и проверь, что нет ошибок).
- [ ] `xcodebuild -project Garage.xcodeproj -list` — в схемах появилась `<Name>`.
- [ ] `git diff Garage.xcodeproj/project.xcproj` — только `packages` и `package-product-members`, ничего лишнего.
- [ ] Никакого `project.pbxproj`.

## 4. Обновить CLAUDE.md

- [ ] **Architecture**: пункт про `Packages/<Name>` — назначение, платформы, зависимости, как тестируется.
- [ ] **Commands**: команда тестов пакета.
- [ ] **Rules** (при необходимости): ограничения импортов (Linux), «X только в `<Name>`» и т. п.
- [ ] **`scripts/check.sh`**: добавить пакет в нужную секцию — Linux-пакет в список `swift test` секции `linux`, iOS-only пакет в `xcodebuild test` секции `ios`. В «Definition of done» CLAUDE.md ничего добавлять не нужно.

## 5. Definition of done

Запустить `scripts/check.sh` (после шага 4) и выполнить остальные пункты из `CLAUDE.md` → «Definition of done»; сообщить о каждом пункте.
