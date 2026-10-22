# Выпуск установщиков

1. Обновите версию в `pyproject.toml` и `src/ctrlcctrlv/__init__.py`, добавьте `docs/releases/v<версия>.md`.
2. Отправьте исходники в GitHub и дождитесь успешного workflow **Desktop checks and builds** для обеих платформ. Он проверяет Windows Setup и копию приложения из Mac DMG.
3. Создайте тег `v<версия>` на точном коммите успешной сборки и отправьте тег.
4. Запустите **Publish verified desktop release** с этим тегом и ID успешного запуска сборки. Например: `gh workflow run release.yml -f tag=v0.1.1 -f build_run_id=38380193396`.

Публикация сверяет коммит, версию, обе платформы, имена пакетов и SHA256; сначала загружает все файлы в draft, затем сверяет полученные GitHub контрольные суммы и публикует релиз. При обрыве можно повторить запуск для того же draft. Уже опубликованные релизы этот workflow не изменяет. Установочные пакеты и ключи подписи в Git не добавляются.

## Rust-версия

1. Обновите `version` в `[workspace.package]` корневого `Cargo.toml` и добавьте `docs/releases/v<версия>.md`.
2. Создайте тег `v<версия>` на коммите в `main` и отправьте его: `git tag v0.2.0 && git push origin v0.2.0`.
3. Workflow **Publish Rust desktop release** сам проверяет тег и версию, собирает установщик `ctrlCctrlV-<версия>-windows-x64-setup.exe` (NSIS, для текущего пользователя, обновляет установленную версию), переносимый `ctrlCctrlV-<версия>-windows-x64.exe` и `ctrlCctrlV-<версия>-macos-arm64.dmg`, сверяет SHA256, загружает draft и публикует его как pre-release. Пока Rust-версия предварительная, «latest» остаётся за Python-выпуском.
