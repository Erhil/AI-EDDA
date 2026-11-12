# Зависимости

Python-зависимости закреплены в [uv.lock](../uv.lock), frontend-зависимости — в [web/package-lock.json](../web/package-lock.json).
Устанавливайте их штатными командами из [README](../README.md), сохраняя выбранный профиль CPU или CUDA.

| Часть | Компоненты | Назначение |
|---|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic | HTTP API, проверка данных и хранение проектов |
| Анализ | Beat This, PyTorch, TorchAudio | Доли и начала тактов |
| DSP | NumPy, SciPy, librosa | Атаки, энергия и признаки ритма |
| Аудио | SoundFile, libsndfile, SoXR | Чтение, преобразование и канонический PCM |
| Обложки | Pillow | Подготовка изображения для игрового формата |
| UI | React, TypeScript, Vite, wavesurfer.js | Редактор, сборка и waveform |
| Demo | MSW | Ответы API в браузере без Python-сервера |
| Проверки | pytest, Ruff, Node.js test runner, Playwright | Backend, frontend и браузерные проверки |

Для запуска собранного приложения Node.js не нужен. Для разработки интерфейса нужен Node.js 24 и npm.
Демонстрационный UI не требует Python, GPU или checkpoint. Настоящий анализ требует отдельно загруженную модель.

## Модель

Используется Beat This final0. [MODEL_MANIFEST.json](../MODEL_MANIFEST.json) задаёт источник, размер, SHA-256 и лицензию весов.
Загрузчик проверяет размер и hash до публикации файла. Модель не включается в Git и wheel.
Команды проверки приведены в [MODEL_CHECKS.md](MODEL_CHECKS.md).

## Лицензии

Собственный код AI-EDDA распространяется под [GPL-3.0-only](../LICENSE).
Лицензии зависимостей и весов остаются отдельными. Их перечень и обязательные тексты содержатся в [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
Python и CUDA binaries устанавливаются из выбранных package indexes; они не копируются в исходный архив AI-EDDA.
Права на пользовательскую музыку и обложки не передаются вместе с лицензией приложения.

