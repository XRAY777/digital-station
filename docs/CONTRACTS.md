# Контракты цифровой станции

Этап 1 · версия проекта контрактов 1 · 2 октября 2026 года.

Это контракт продукта и target API. Реализованы `/health/live`, `/health`, `/api/stage`, auth/CSRF endpoints и базовое управление пользователями. Station-команды, история и поток `/ws/station` остаются будущими интерфейсами. [Архитектура](ARCHITECTURE.md) задаёт владельцев данных; [приёмка](ACCEPTANCE.md) определяет проверки.

Health component принимает `ok`, `offline`, `not_implemented`, `degraded` или `unavailable`. Необязательный AI может быть offline при общей готовности 200; недоступность обязательной БД/Runtime возвращает 503; работа с деградировавшим, но безопасным optimizer описывается явно.

## 1. Соглашения и версии

API prefix `/api`, станция первого демо одна, но все station DTO имеют stationId. GET-списки возвращают `{items, nextCursor}`; предел page size 200, default 50. Сортировки и фильтры имеют allowlist. Ошибки не раскрывают SQL, stack trace, cookie или секреты. ID — непрозрачные строки до 128 символов; человекочитаемый номер поезда не используется как ключ безопасности.

| Поле | Значение |
| --- | --- |
| schemaVersion | Версия транспортного контракта, начальная 1; несовместимая версия требует обновления клиента |
| stationId | Стабильный идентификатор станции |
| simulationRunId | Новый UUID при RESET, не меняется при обычном restart процесса |
| stateVersion | Увеличивается на 1 при каждом committed-изменении публичного состояния run |
| controlRevision | Увеличивается при изменении маршрутов/занятия/доступности/расписания/clock mode/приоритетов/конфигурации/ресурсов |
| sequence | Последовательный номер transaction frame внутри run; не номер отдельного под-события |
| topologyVersion / configVersion | Неизменяемые версии топологии и параметров, использованные для расчёта |
| simulationTimeMs | Время run в миллисекундах; 0 при seed, не равно реальному Unix time |
| occurredAt / serverTime | UTC RFC 3339; для журнала, сети и эксплуатации |

Чистое продвижение координат/скорости и производных метрик внутри неизменившихся блоков меняет stateVersion/sequence, но не controlRevision. Вход/выход блока, изменение authority, существенное опоздание по настраиваемому порогу, смена clock rate или отказ ресурса меняют controlRevision. Изменение последних миллисекунд численной delay само по себе не запускает бесконечное replanning.

Публикация кандидатов плана меняет stateVersion/sequence, но не controlRevision; иначе опубликованный результат сразу инвалидировал бы сам себя. Auth/audit, доступные только ADMIN, имеют отдельные последовательности хранения и не создают дыр в общем station stream. Все счётчики JSON — неотрицательные целые <= 2^53-1; неизвестное поле критической команды отклоняется, а не игнорируется.

## 2. Auth и RBAC

Все station/read/write endpoint-ы требуют сессию. Исключения: статические login-assets, `/health/live`, обезличенный `/health`, `/api/auth/csrf` и `/api/auth/login`. OpenAPI/Swagger после сборки доступны аутентифицированному пользователю и используют локальные assets.

Access и refresh — случайные непрозрачные токены в HttpOnly cookie, без токенов в localStorage и URL. Refresh cookie ограничивается путём `/api/auth`; access — `/`. В HTTPS-профиле Secure=true, SameSite=Strict; Domain не задаётся. Loopback demo имеет отдельные имена cookie и единственное исключение Secure=false. TLS termination и trusted proxy headers задаются allowlist-конфигурацией, клиент не может подменить свой IP произвольным X-Forwarded-For.

`GET /api/auth/csrf` создаёт или использует pre-auth/session context и возвращает synchronizer token. Login/refresh/logout и другие изменяющие browser-запросы требуют `X-CSRF-Token` и допустимый Origin. После смены сессии token обновляется. CORS по умолчанию same-origin, в development разрешены только явно указанные локальные origins; wildcard с credentials запрещён. Пароли хешируются Argon2id; токены хранятся хешированными, ограничения входа действуют по account и IP.

| Возможность | OBSERVER | DISPATCHER | ADMIN |
| --- | --- | --- | --- |
| Live, история, расписание, индекс, CSV/PDF | Да | Да | Да |
| Просмотр конфликтов и предложенных планов | Да | Да | Да |
| Маршруты, стрелки, train hold/release | Нет | Да | Да |
| Clock, сценарии, инциденты, emergency stop | Нет | Да | Да |
| Расчёт/принятие/отклонение плана | Нет | Да | Да |
| Пользователи, роли, настройки, audit | Нет | Нет | Да |

Роль берётся из серверной сессии/БД. ADMIN также проходит safety. Logout отзывает session family и закрывает связанные WebSocket; refresh требует активного family. При понижении роли pending-команды повторно авторизуются при исполнении. WS не принимает команды движения. Проверка Origin, лимитов и жизненного цикла сессий соответствует [OWASP WebSocket Security](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html).

## 3. Изменяющие команды

Все команды станции требуют `Idempotency-Key` и поля `stationId`, `simulationRunId`, `expectedControlRevision`. Общая identity-команды: actorId + stationId + simulationRunId + HTTP method + canonical path + key; сохраняется хеш канонического тела. Тот же ключ с другим телом — 409 IDEMPOTENCY_KEY_REUSED. Совпавший завершённый запрос возвращает прежний результат с `Idempotent-Replay: true`, если действующая сессия всё ещё имеет право на эту операцию. Повтор не добавляет второй domain event.

Проверка ключа выполняется до проверки expectedControlRevision, но после проверки личности и прав. Одновременные повторы сериализуются Runtime и уникальным ограничением command_results. При оборванном соединении клиент повторяет тот же запрос/ключ. Таймаут не доказывает, что команда не выполнилась. Терминальные бизнес-результаты сохраняются на срок жизни run; replay ответа не означает, что его stateVersion остаётся текущей. Клиент не откатывает store к более старому HTTP-ответу.

Пример `POST /api/routes` — создание REQUESTED-маршрута, без открытия сигнала:

```json
{
  "stationId": "demo-station",
  "simulationRunId": "b06ecfe0-0152-477d-a30b-49190bc65c6f",
  "expectedControlRevision": 17,
  "trainId": "train-3001",
  "originNodeId": "west-entry",
  "destinationTrackId": "track-05",
  "operationType": "ARRIVAL"
}
```

ID блоков, позиция стрелок и разрешение сигнала не принимаются от клиента как готовое безопасное решение: сервер строит маршрут сам. `POST /api/routes/{id}/reserve` проходит актуальную проверку, резервирует весь набор и инициирует установку стрелок. RESERVED не означает GREEN, пока стрелки в MOVING. Ответ после commit:

```json
{
  "requestId": "req-reserve-001",
  "commandId": "cmd-001",
  "stationId": "demo-station",
  "simulationRunId": "b06ecfe0-0152-477d-a30b-49190bc65c6f",
  "stateVersion": 125,
  "controlRevision": 19,
  "sequence": 125,
  "result": { "routeId": "route-001", "status": "RESERVED" }
}
```

HTTP 201 — новый ресурс, 200 — завершённая команда, 202 — только фоновая задача расчёта/отчёта. Начальные reserve/cancel/accept выполняются синхронно и не отвечают «применено» до commit. При техническом таймауте эффект проверяется повтором идемпотентной команды.

| HTTP | Код | Значение |
| --- | --- | --- |
| 401 | SESSION_INVALID / SESSION_EXPIRED | Нет действующей сессии |
| 403 | FORBIDDEN / CSRF_FAILED / ORIGIN_REJECTED | Недостаточно прав или не пройдена защита браузерного запроса |
| 404 | ENTITY_NOT_FOUND | Объект отсутствует в доступной станции/run |
| 409 | STALE_REVISION / RUN_MISMATCH / IDEMPOTENCY_KEY_REUSED | Версия, run или повтор команды несовместимы |
| 409 | SAFETY_CONFLICT / ROUTE_LOCKED / PLAN_EXPIRED | Команда недопустима по текущей модели |
| 422 | VALIDATION_ERROR | Неверные поля, типы, диапазоны, направление или параметры |
| 429 | RATE_LIMITED | Лимит запросов, с Retry-After |
| 503 | STORAGE_UNAVAILABLE / RUNTIME_UNAVAILABLE / QUEUE_FULL | Нет возможности согласованно выполнить команду |

Ответ ошибки:

```json
{
  "error": {
    "code": "SAFETY_CONFLICT",
    "message": "Блок занят другим поездом",
    "requestId": "req-reserve-002",
    "currentControlRevision": 19,
    "retryable": false,
    "details": {
      "invariantId": "I01",
      "blockIds": ["TRACK_05_A"],
      "trainIds": ["train-3001", "train-2002"]
    }
  }
}
```

## 4. Поверхность REST API

В строках ниже `{id}` — проверяемый сервером ID ресурса. Все изменяющие station endpoints используют общий command envelope; auth и user administration имеют свои session/version контракты.

| Метод и путь | Назначение / право |
| --- | --- |
| GET `/api/auth/csrf`; POST `/api/auth/login`, `/refresh`, `/logout`; GET `/api/auth/me` | Сессии; сокращённые пути login/refresh/logout имеют prefix `/api/auth` |
| GET `/api/station/state` | Snapshot и watermark; все роли |
| GET `/api/station/topology` | Граф/геометрия конкретной topologyVersion; все роли |
| GET `/api/trains`, `/api/trains/{id}` | Текущие поезда и инспектор; все роли |
| GET `/api/tracks`, `/api/blocks`, `/api/switches`, `/api/signals`, `/api/platforms` | Инфраструктура; все роли |
| GET `/api/routes`; POST `/api/routes` | Чтение / создание REQUESTED; запись DISPATCHER+ |
| POST `/api/routes/{id}/reserve`, `/api/routes/{id}/cancel` | Атомарная резервация / запрос безопасной отмены |
| POST `/api/switches/{id}/position` | Запрос STRAIGHT/DIVERGING с safety; при блокировке 409 |
| POST `/api/trains/{id}/hold`, `/api/trains/{id}/release` | Удержание/возобновление; без произвольной установки координат |
| GET `/api/conflicts`; POST `/api/conflicts/{id}/resolve` | Чтение / повторная проверка устранения причины |
| GET `/api/plans`; POST `/api/plans/recalculate`; GET `/api/plan-jobs/{id}` | План и фоновый расчёт |
| POST `/api/plans/{id}/accept`, `/api/plans/{id}/reject` | Решение диспетчера по variantId; повторная safety-проверка |
| GET `/api/recommendations`; POST `/api/assistant/explain` | Объяснения существующих вариантов, без исполнения команд |
| GET `/api/schedules`, `/api/resources` | Расписание, locomotives и crews |
| GET `/api/simulation`; POST `/api/simulation/control` | START/PAUSE/RESUME/STOP/RESET и SET_SPEED; DISPATCHER+ на запись |
| GET `/api/scenarios`; POST `/api/scenarios/{id}/start` | Десять сценариев из требований |
| GET `/api/incidents`; POST `/api/incidents`; POST `/api/incidents/{id}/resolve` | Инциденты и устранение причины; связь с повторным расчётом |
| GET `/api/metrics/efficiency` | score, coverage, raw factors, contributions, версия формулы |
| GET `/api/history/runs`; GET `/api/history` | Список run; исторический snapshot на simulationTimeMs и события |
| POST `/api/reports`; GET `/api/reports/{id}`; GET `/api/reports/{id}/download` | Задача CSV/PDF, её статус и результат; все роли в пределах доступных данных |
| GET `/api/audit`; GET/POST `/api/users`; PATCH `/api/users/{id}` | ADMIN; аудит, пользователи и роли |
| GET/PATCH `/api/settings`; GET `/api/system/info` | ADMIN; версии настроек и диагностика |
| GET `/health/live`, `/health` | Проверка процесса / готовности; обезличенный ответ |

Нет endpoint-а «установить GREEN» или «занять блок произвольным поездом». Сигналы и занятие вычисляются Runtime. Reports POST создаёт экспорт и потому требует CSRF/idempotency, но не controlRevision live-станции; его вход содержит run, диапазон времени и формат. Повтор export job не создаёт дубли. Административные user/settings PATCH используют собственную expectedVersion и Idempotency-Key. Station-affecting settings дополнительно требуют station command envelope и применяются через Runtime: версия настроек, controlRevision и история обновляются одной транзакцией, без предварительной отдельной записи активной конфигурации из API.

## 5. Snapshot и WebSocket

Endpoint `/ws/station`: same-origin browser cookie-auth, проверка Origin и сессии до принятия подписки. Сервер периодически перепроверяет expiry/revocation; клиент refresh выполняет по REST. Любое неизвестное сообщение, попытка исполнять команды через WS, слишком большой payload или превышение лимита отклоняются. Inbound limit по умолчанию 64 KiB, outbound frame budget 2 MiB, лимиты конфигурируемы.

Клиент отправляет SUBSCRIBE с stationId, simulationRunId?, lastSequence?. Snapshot включает schemaVersion/station/run/topology/config, stateVersion, controlRevision, sequence, simulationTimeMs, serverTime, clock, trains, tracks, blocks, switches, signals, platforms, routes, resources, schedules, conflicts, incidents, plans и efficiency. DTO excludes hashes/tokens/private audit. Геометрия большой топологии загружается по topologyVersion отдельно; UI не совмещает состояние с другой геометрией.

Начальная синхронизация: подписчик регистрируется на будущие frames, получает committed snapshot с watermark N, буфер отбрасывает frames <= N, затем выдаёт frames N+1 по порядку. Snapshot и live не читаются из разных несогласованных версий. При overflow буфера сервер отправляет RESYNC_REQUIRED и требует новый snapshot.

Один FRAME отражает одну транзакцию. События и entityChanges применяются атомарно; UI не может увидеть занятый блок и ещё не обновлённую позицию в разных renders. Пример изменения скорости, не меняющего управляющую ревизию:

```json
{
  "type": "FRAME",
  "schemaVersion": 1,
  "stationId": "demo-station",
  "simulationRunId": "b06ecfe0-0152-477d-a30b-49190bc65c6f",
  "sequence": 126,
  "baseStateVersion": 125,
  "stateVersion": 126,
  "controlRevision": 19,
  "simulationTimeMs": 42100,
  "occurredAt": "2026-10-02T03:00:42.100Z",
  "events": [
    {
      "eventId": "b06ecfe0-0152-477d-a30b-49190bc65c6f:126:0",
      "type": "train.speed.updated",
      "entityId": "train-3001",
      "payload": { "currentSpeedMps": 0.5 }
    }
  ],
  "entityChanges": [
    {
      "collection": "trains",
      "operation": "MERGE",
      "id": "train-3001",
      "values": { "currentSpeedMps": 0.5 }
    }
  ]
}
```

`MERGE` обновляет указанные верхнеуровневые поля существующего объекта, вложенное значение заменяется целиком; null — явное значение. Для новой сущности используется `PUT` с полным DTO, для удаления `REMOVE` без values. MERGE неизвестного ID — resync, а не создание частичной сущности. Список коллекций и полей задаётся схемой; reflection и исполнение путей из произвольного JSON запрещены. `events` — семантика для журнала, `entityChanges` — единственный механизм обновления client projection, чтобы не применять одно изменение дважды.

Event types включают train.position.updated, train.speed.updated, train.delay.updated; route.created/reserved/cancelled/activated/completed; block.occupied/released; switch.changed; signal.changed; conflict.detected/resolved; plan.recalculated/accepted/rejected; station.efficiency.updated; incident.created/resolved; simulation.clock.changed. Изменение нескольких связанных сущностей помещается в один FRAME. HEARTBEAT, SNAPSHOT, RESYNC_REQUIRED — управляющие сообщения транспорта вне последовательности domain frames.

Правила клиента:

1. Другой run или неизвестная schemaVersion не применяется к текущему store. RESET вызывает RESYNC_REQUIRED с новым run и новый snapshot.
2. sequence <= lastSequence игнорируется как повтор. sequence > lastSequence+1 или неверная baseStateVersion требуют resync.
3. Новый snapshot атомарно заменяет проекцию и watermark; устаревшие HTTP-ответы не могут откатить его.
4. Reconnect использует exponential backoff 1–30 с и jitter. Сервер переотправляет retained frames, иначе snapshot. LastSequence не берётся на веру как доказательство клиентского state: сомнение ведёт к snapshot.
5. Координаты можно объединять внутри ещё не committed-пакета. Нельзя выбросить committed-frame и оставить дырку в sequence; медленный клиент получает resync.
6. HEARTBEAT каждые 5 с; возраст входящего потока > 2 с при RUNNING — STALE, отсутствие heartbeat 15 с — CONNECTION_LOST. В PAUSED свежесть оценивается по heartbeat. Значения настраиваются.
7. simulationTimeMs монотонно не убывает внутри run; timestamp проверяется по формату и разумному дрейфу, но системное время клиента не определяет порядок. Неправдоподобный UTC вызывает диагностику/resync, не «исправление» координат.

## 6. Контракт оптимизации

Вход OptimizationRequest: jobId, station/run, baseStateVersion, baseControlRevision, topologyVersion/configVersion, simulationTimeMs, horizonEndMs, immutable model, текущий план, причина пересчёта, budgetMs и seed. Копия не содержит сессий и секретов.

Выход: jobId, status = OPTIMAL/FEASIBLE/INFEASIBLE/TIMEOUT/ERROR, elapsedMs, source versions, variants[]. У варианта: id, упорядоченные операции и assignments, changeSet, validFrom/validUntil simulationTime, прогноз задержек/индекса, причины, ограничения, оценка objective и solver provenance. TIMEOUT может иметь отдельно помеченные FEASIBLE-варианты только после валидации; ERROR не превращается в вымышленные результаты.

На ACCEPT сервер проверяет run, topology/config, соответствие клиентского expectedControlRevision текущему и совпадение variant.baseControlRevision с ним. Рост только stateVersion не требует отклонения: позиция и тормозная достижимость заново проверяются по текущему снимку. Даже при совпадении управляющей версии истёкшее окно или уже недостижимое время операции вызывает PLAN_EXPIRED/SAFETY_CONFLICT. Отличающийся controlRevision означает 409 и новый расчёт, без молчаливого применения старого варианта.

Отклонение сохраняет решение диспетчера. «Показать на схеме» — локальная подсветка planned geometry, без серверных резерваций. Принятые будущие операции проходят safety повторно при наступлении времени исполнения. Задачи расчёта с устаревшим run/revision могут сохраняться в истории, но не заменяют текущий набор актуальных рекомендаций.

## 7. History, reports и health

History query: stationId, simulationRunId, atSimulationTimeMs и optional sequence для неоднозначного simulationTime; ответ — snapshot, watermark и ссылки на нужную topology/configVersion. Если точка удалена retention-политикой, 410 HISTORY_EXPIRED с доступным диапазоном; нет молчаливой подстановки live. При точке позже конца run — 422.

Отчёт фиксирует run, `[fromSimulationTimeMs, toSimulationTimeMs)` и cutoffSequence на момент принятия export job. CSV/PDF считаются из одинакового снимка/истории, поэтому повтор не захватывает новые live-события. Экспорт включает поезда, задержки, throughput, конфликты, incidents, индекс, использованные пути и рекомендации. CSV экранирует значения с формульными префиксами; PDF использует локальный шрифт с кириллицей.

Пример готовности системы с отключённым LLM, HTTP 200:

```json
{
  "status": "ready",
  "backend": "ok",
  "database": "ok",
  "simulation": "ok",
  "optimizer": "ok",
  "websocket": "ok",
  "ai": "offline"
}
```

`websocket=ok` означает готовность подсистемы публикации, а не наличие хотя бы одного клиента. Проверка внешнего AI не входит в критический readiness path. На этапе skeleton ещё не реализованные компоненты обозначаются `not_implemented`, а не `ok`.
