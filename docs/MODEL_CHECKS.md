# Проверка модели и аудио

Выполните установку из [README](../README.md), затем загрузите checkpoint:

```powershell
.\.venv\Scripts\ai-edda.exe download-model
.\.venv\Scripts\ai-edda.exe doctor --device cuda
.\.venv\Scripts\python.exe scripts/model_smoke.py --require-cuda
```

Для CPU используйте `doctor --device cpu` и уберите `--require-cuda` у model smoke.
Профиль Python выбирается при установке: `./setup.ps1 -Device cpu` или `-Device cuda`.
Не переключайте профиль случайной командой `uv sync` без соответствующего extra.

Smoke создаёт синтетическое аудио, преобразует его в OGG, декодирует конечный OGG и запускает настоящий Beat This.
Он проверяет конечность массивов и сохраняет JSON с результатами в `docs/scratch/implement/model-smoke/results.json`.
Это создаваемые при проверке локальные данные; в исходном checkout их нет. Они исключены из Git.
Скрипт использует стандартный `%LOCALAPPDATA%\AI-EDDA\models\beat-this\final0.ckpt`.
Проверка CUDA завершается ошибкой, если обязательное устройство недоступно.

## Regression tests с моделью

После установки developer dependencies:

```powershell
$env:AI_EDDA_TEST_CHECKPOINT = "$env:LOCALAPPDATA\AI-EDDA\models\beat-this\final0.ckpt"
.\.venv\Scripts\python.exe -m pytest tests/test_analysis_worker.py tests/integration/test_beat_analysis.py -q
```

Если checkpoint отсутствует, проверки настоящего inference пропускаются с явной причиной.
Проверки отмены и импорта Python-модуля без загрузки Torch выполняются независимо от checkpoint.
CPU-прогон не подтверждает CUDA OOM recovery; для него нужна CUDA-среда.

## Границы результатов

V0 проверялась на CPU и RTX 4090 с аудио 30, 300 и 600 секунд. Эти измерения не подтверждают работу RTX 3060 6 ГБ.
Успешный inference также не подтверждает музыкальную точность или удобство полученной карты.
Проверка в Ragnarock и оценка человеком остаются отдельными действиями.
