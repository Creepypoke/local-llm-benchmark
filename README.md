# Local LLM Benchmark

Изолированная установка готового официального llama.cpp и воспроизводимый streaming-бенчмарк Qwen3.5-9B от Unsloth. Docker и чужие установки не используются. Setup автоматически определяет ОС, архитектуру и backend, скачивает готовый официальный бинарник и проверяет SHA-256 и commit. Компиляции нет. Для запуска нужны Python 3.12+, Node.js 22.13+ с npm и, для NVIDIA, установленный драйвер.

## Получение проекта

```bash
git clone git@github.com:Creepypoke/local-llm-benchmark.git
cd local-llm-benchmark
```

В Git хранятся исходники, настройки, manifest официальных бинарников с SHA-256, lock-файл npm и общие входные данные бенчмарка. Готовые бинарники и библиотеки в `runtime/`, модели, `.venv`, `node_modules`, кеши, отчёты и логи создаются локально и исключены через `.gitignore`. Файлы `config/runtime.json`, `config/model-lock.json` и `config/python-environment.txt` также не публикуются. Setup создаёт нужные директории и метаданные установленного бинарника/модели.

`benchmarks/long_context.txt` и `benchmarks/long_context.json` включены как фиксированные тестовые данные: все компьютеры получают один и тот же вход, проверенный токенизатором модели.

## Быстрый запуск

На всех платформах нужны Python 3.12+ и Node.js 22.13+ с npm. Git нужен только для клонирования этого проекта; можно скачать его ZIP-архив.

- **macOS:** автоматически выбирается ARM64 + Metal для Apple Silicon, x64 + CPU для Intel. Xcode/Command Line Tools не нужны.
- **Windows 11:** при наличии NVIDIA выбирается CUDA-пакет x64/ARM64, иначе CPU. CUDA runtime DLL скачиваются из официального релиза рядом с сервером. CUDA Toolkit, Visual Studio Build Tools и Windows SDK не нужны. Если Windows сообщает об отсутствующем MSVC runtime, установите Microsoft Visual C++ Redistributable.
- **Linux:** выбирается x64/ARM64 + CUDA при наличии NVIDIA, иначе CPU. CUDA runtime `.so` также скачиваются автоматически. Linux должен быть совместим с официальными Ubuntu-бинарниками релиза (glibc и системные библиотеки). Если бинарник не запускается из-за ABI/glibc, setup сообщает ошибку; сборки или замены на другую версию нет.

Версия CUDA выбирается из доступных пакетов по максимальной версии, которую поддерживает установленный драйвер (`nvidia-smi`). При слишком старом драйвере setup просит обновить его, не подменяя CUDA на CPU. Компиляторы, CMake, Ninja и `nvcc` не используются.

```bash
cd local-llm-benchmark
./scripts/setup.sh
./scripts/start-server.sh
.venv/bin/python benchmark.py --all
./scripts/stop-server.sh
```

```powershell
cd local-llm-benchmark
.\scripts\setup.ps1
.\scripts\start-server.ps1
.\.venv\Scripts\python.exe benchmark.py --all
.\scripts\stop-server.ps1
```

`setup` также устанавливает локальный TypeScript через `npm ci`. Никакие npm lifecycle scripts не запускаются. Для запуска с определённым Python: `PYTHON=python3.12 ./scripts/setup.sh`. Не запускайте несколько копий benchmark параллельно против одного сервера.

Сервер работает только на `http://127.0.0.1:18080`, health: `GET /health`. Проверка занятого порта не останавливает посторонний процесс. Повторный start проверяет свой PID; stop проверяет PID, время создания и командную строку перед остановкой. После жёсткого завершения машины stale PID не принимается за свой сервер.

## Зафиксированные версии

Зафиксирован стабильный **v1.0.0**, commit **b1b770d90d94cad9e9ea5b993bdbc383a522fb28**. У стабильного релиза файл [`nightly-tag.txt`](https://github.com/ggml-org/llama.cpp/releases/download/v1.0.0/nightly-tag.txt) указывает на [`b12359`](https://github.com/ggml-org/llama.cpp/releases/tag/b12359), где опубликованы бинарники того же commit.

Список поддерживаемых официальных архивов, их URL и SHA-256 хранится в `config/llama-release.json`. Setup не клонирует llama.cpp и не вызывает инструменты сборки. Архивы кешируются, распаковываются внутрь `runtime/llama.cpp/`; CUDA runtime копируется рядом с сервером. Перед активацией проверяются checksum архива, `llama-server --version` и `--list-devices`. Повторный setup использует уже установленный пакет.

Модель `unsloth/Qwen3.5-9B-GGUF`, revision **3885219b6810b007914f3a7950a8d1b469d598a5**, файл `Qwen3.5-9B-UD-Q5_K_XL.gguf`, SHA-256 **96cf42ddb97f9572410a72b9ed6f2299b1e887ee08da4c2a6c01e897cfa9f673**. Скачивается только текстовая GGUF; vision/mmproj не нужен.

Для нового исследования обновите release/commit в YAML и manifest архивов с SHA-256 в `config/llama-release.json` после проверки [официальных releases](https://github.com/ggml-org/llama.cpp/releases/latest), перепроверьте API на новом SHA и выполните setup. Сравнение разных commit помечается как нестрогое. Переносите `config/benchmark.yaml`, `config/llama-release.json`, `config/model-lock.json`, `package-lock.json`, `requirements.txt`, `benchmarks/tasks.json`, `benchmarks/long_context.txt` и `benchmarks/long_context.json` между устройствами.

## Конфигурация и CLI

Все параметры эксперимента — `config/benchmark.yaml`; задачи, эталоны и индивидуальные output budgets — `benchmarks/tasks.json`. По умолчанию: context 32768, GPU layers 999 (полный offload, если backend позволяет), Flash Attention on, batch 512, ubatch 256, parallel 1, temperature 0, seed 42, max_tokens 8192 в обоих режимах, один полученный ответ на каждую задачу и режим. Ограничение 8192 включает reasoning и итоговый текст; budget thinking отдельно неограничен. Достижение output limit — `truncated`, а не завершённый ответ. Это операционное определение MAX, без неподдерживаемого Qwen уровня `effort=max`.

```bash
.venv/bin/python benchmark.py --all
.venv/bin/python benchmark.py --task 3
.venv/bin/python benchmark.py --mode thinking
.venv/bin/python benchmark.py --mode no-thinking
.venv/bin/python benchmark.py --all --output reports/mac-m1pro
.venv/bin/python benchmark.py --probe-only --output reports/probe
.venv/bin/python benchmark.py --task 1 --output reports/smoke
.venv/bin/python benchmark.py --all --no-judge --output reports/manual
```

Используйте новый output для каждого эксперимента: существующий results.json не перезаписывается. `--config path.yaml` доступен benchmark и management-командам (`scripts/start-server.sh --config ...`). При изменении server-конфига перезапустите сервер. При сбое сохраняются `failure.json`, частичный отчёт и уже выполненные запросы; ошибка не скрывается. Не удаляйте частичные измерения, если они относятся к исследованию. После диагностики продолжите через `--resume`, чтобы не отправлять заново уже полученные запросы.

## Thinking и prompt cache

Механизм проверен по исходникам **зафиксированного** release:

- [server README](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/server/README.md): per-request `chat_template_kwargs`, `reasoning_effort=none`, stream `timings`, `/apply-template`, `/tokenize`.
- [server-common.cpp](https://github.com/ggml-org/llama.cpp/blob/b1b770d90d94cad9e9ea5b993bdbc383a522fb28/tools/server/server-common.cpp): bool `enable_thinking` override и `reasoning_budget_tokens`.
- [модель Unsloth](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) и встроенный Jinja в `/props`.

OFF: `chat_template_kwargs.enable_thinking=false`, `reasoning_effort=none`. MAX: `enable_thinking=true`, `reasoning_budget_tokens=-1`, server budget `-1`. На сервере thinking глобально не отключается. `reasoning_format=deepseek` разделяет `reasoning_content` и `content`.

Перед измерениями два реальных probe-запроса и `/apply-template` подтверждают разные шаблоны, пустой закрытый think-prefix для OFF, открытый prefix для MAX, отсутствие reasoning OFF и наличие reasoning MAX. Неверный ответ модели не является доказательством неисправного переключения. Неподтверждённый режим останавливает эксперимент с сохранением диагностики. OFF проверяется и у каждого измеряемого ответа.

Кеш: `--cache-ram 0 --cache-reuse 0`, каждый запрос `cache_prompt=false`. `timings.cache_n` или `usage.prompt_tokens_details.cached_tokens` должен быть 0 для каждого измерения, иначе runner падает с ошибкой. Прогрев выполняется отдельно в обоих режимах, не входит в статистику. История всегда содержит одно user message. Прогрев не означает кеширование промпта.

## Метрики и ограничения

Основной источник — `timings` конкретного запроса: prompt_ms, prompt_per_second, predicted_ms, predicted_per_second, prompt_n, predicted_n. Агрегированные `/metrics` не используются. Исходные SSE, timestamps, usage, request и timings сохраняются полностью. Output / total_generated_tokens — все predicted tokens, включая reasoning и служебные tokens.

TTFT — время от отправки HTTP-запроса до первого **непустого** delta с текстом/рассуждением. Первый role-only, пустой или usage chunk не считается токеном. Отдельно сохраняются TTFT reasoning и visible answer. Это измеренное сетевое приближение сверху к token TTFT: special tokens и буферизация parser не видны клиенту. Prefill throughput никогда не вычисляется из полного времени запроса.

Если API не сообщает точные reasoning_tokens/final_answer_tokens, поля остаются null. Повторная токенизация отдельных строк не даёт точного распределения generated tokens и не используется как замена. Для очень коротких ответов серверный decode timing может исключать генерацию первого токена; сырые timings сохранены.

RAM/RSS и CPU процесса семплируются psutil; RSS не равен полному GPU allocation. NVIDIA дополнительно семплируется nvidia-smi, память и загрузка GPU общие для устройства. Семплированный peak может пропустить мгновенный пик. На Metal достоверные GPU memory/utilization отсутствуют и не подставляются; указывается unified RAM. Сторонняя нагрузка и температурный throttling могут повлиять на результаты: для строгого сравнения запускайте эксперимент в одинаковых условиях питания/нагрева на свободных устройствах.

Каждая задача выполняется один раз в каждом режиме. Для одного наблюдения mean/median/min/max совпадают, standard deviation и P95 не оцениваются (null); независимые повторы для статистики не выполняются. Ошибочный ответ, невалидный JSON и finish_reason=length не вызывают повторного inference. Только обрыв SSE, сетевой таймаут, HTTP 408/429/5xx разрешают до `benchmark.technical_retries=2` дополнительных попыток с паузой `retry_delay_seconds=2`. Неудачные технические попытки сохраняются отдельно и не входят в метрики полученного ответа.

Продолжение остановленного эксперимента: `.venv/bin/python benchmark.py --all --resume --output reports`. Уже полученные ответы пропускаются независимо от правильности; повторные probe и warmup при продолжении не выполняются, если режимы ранее подтверждены. CLI `--repeats` удалён.

## Проверка ответов

1. Арифметика: точное число 1015.
2. Коробки: отдельный local-model judge проверяет выбор смешанной надписи и оба исхода извлечения по явным семантическим критериям с точными исходными строками, выбранными по номерам; ключевые слова не дают pass.
3. TypeScript: код извлекается, компилируется локальным TypeScript, исполняется Node с permission model, запретом запуска дочерних процессов, ограничением heap и timeout. 5 обязательных примеров и 100 детерминированных случайных проверок против O(n²) reference. Отдельно измеряется scaling возрастающих массивов 20k/80k/320k; semantic judge проверяет lower_bound и структуру O(n log n). Scaling сам по себе не доказывает асимптотику. Неисполняемый код или timeout — явный fail.
4. React review: judge проверяет объяснение захвата исходного count, корректность functional updater/альтернативы и cleanup. Семантический вердикт требует ручного аудита при строгой оценке.
5. JSON: parse без Markdown, точная структура, integer values и суммы, русские названия допускают склонение.
6. Attention: автоматическая проверка 600–800 слов, Q/K/V, softmax, scaling, multi-head, числового примера; техническая точность — manual_review, не автоматический pass.
7. Нагрузка: проверка числа и размерностно корректного расчёта, judge принимает эквивалентную формулу.
8. Числа: точные 500 строк без лишнего текста.
9. Контекст: сохранённые 1000 строк, фиксированная первая ERROR LOG-00931, около 16000 токенов (точное число в long_context.json). Детерминированные timestamps, уровни и IDs. Длина проверяется `/tokenize` загруженной модели. Идентификатор проверяется точно.

Judge получает строгую JSON-схему и отдельный лимит `validation.judge_max_tokens=2048`; количество строк-доказательств и длины объяснений также заданы в YAML. Это ограничение относится только к проверке и не ограничивает reasoning измеряемой модели. Judge-запросы независимы, не включаются в latency/throughput и сохраняются `*-judge.json`. Это предварительная оценка **той же моделью**, возможны ошибки и коррелированное смещение. Для значимых выводов аудируйте цитаты/код. Judge выбирает номера строк, программа проверяет их диапазон и сама извлекает оригинальные цитаты. Переписывать цитаты модели не требуется. Некорректный JSON/цитата judge → manual_review, а не выдуманный pass. `--no-judge` оставляет семантические пункты на ручную проверку. Accuracy = pass / число полученных ответов, pending показывается отдельно; manual_review не трактуется как доказанная ошибка. Токенный лимит всегда делает попытку неуспешной независимо от частичного содержимого.

## Результаты и сравнение машин

`reports/` по умолчанию содержит `report.md`, `report.html`, `results.csv`, `results.json`, `raw/`. HTML работает офлайн без CDN: шесть SVG-графиков, таблицы ответов/производительности/изменений MAX, все ответы и reasoning без обрезки в details, ссылки на raw. В таблице рядом с эталоном и ответами показан полный исходный промпт. Длинные задачи, включая входные 16000 токенов логов, доступны в раскрывающейся секции без обрезки. Архивные наборы с повторами по-прежнему читаются; для них репрезентативный ответ выбирается по медиане latency. `raw/source/` содержит исходники, конфигурации, задания и общий контекст с SHA-256 manifest для повторения конкретного эксперимента. Не переносите `.venv`, `node_modules` и `runtime/` между ОС: заново выполните setup. CSV — по строке на измеренный запуск; JSON содержит метаданные и агрегаты.

```bash
.venv/bin/python compare.py \
  reports/windows-rtx5080/results.json \
  reports/mac-m1pro/results.json \
  reports/mac-m5/results.json \
  --output reports/comparison.html
```

Сравнение включает объединение задач и оба режима, явно показывает отсутствующие измерения, все устройства, медианы и относительные различия против первого файла. Проверяются model SHA, квантование, context, generation, batching, GPU offload, FA, кеш, tasks hash, long context hash/токены, commit, validation policy и extra_args. Различие → заметное предупреждение **нестрогой сопоставимости**, сохранённое также в comparison.json. Backend может отличаться по назначению теста; команды и фактические props хранятся в каждом наборе.

## Архитектура

- `llbench/manage.py`: setup, model download, PID-safe lifecycle.
- `llbench/install.py`: определение платформы/backend, выбор официального бинарника, загрузка, SHA-256 и установка CUDA runtime.
- `llbench/client.py`: SSE, per-request timings, TTFT, monitoring, cache verification.
- `llbench/context.py`: один детерминированный input, токенизация сервером.
- `llbench/validate.py`: проверки и семантическая рубрика.
- `scripts/validate-ts.cjs`, `scripts/test-lis.cjs`: отдельное исполнение TypeScript и functional/scaling tests.
- `llbench/report.py`: агрегация, CSV/JSON/Markdown/offline HTML.
- `benchmark.py`: probe → warmup → 9 × 2 (18 ответов), checkpoint каждого результата.
- `compare.py`: проверка совместимости и сравнительный HTML.

Проверки проекта: `.venv/bin/python -m unittest discover -s tests -v`. Локальные результаты проверок и измерений не входят в репозиторий; их сохраняют в `reports/` и `logs/`.

Прогресс текущего полного прогона: `.venv/bin/python scripts/status.py` или `./scripts/status.sh`; журнал `logs/full-benchmark.log`. HTML можно открывать ещё до окончания: он обновляется после каждого завершённого измерения. Статус `running` не означает завершённый полный эксперимент.

Проверка артефактов: `.venv/bin/python scripts/verify-results.py reports --expected-runs 18`. Проверка разработки: `.venv/bin/pip install -r requirements-dev.txt`, затем `.venv/bin/ruff check --select E9,F benchmark.py compare.py llbench scripts/status.py scripts/verify-results.py tests`.
