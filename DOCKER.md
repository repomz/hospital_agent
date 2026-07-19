# Docker image

## Локальная сборка

```bash
docker build \
  --build-arg VERSION=dev \
  --build-arg VCS_REF="$(git rev-parse --short HEAD)" \
  -t hospital-agent:dev .
```

## E2E mock-режим

В mock-режиме агент выполняет настоящий polling и callback через backend, но не подключается к PACS, Yandex, MAPDR и не читает больничные каталоги:

```bash
docker run --rm \
  --name hospital-agent \
  --network viewer-e2e \
  -e HOSPITAL_AGENT_MOCK_COMMANDS=1 \
  -e HOSPITAL_AGENT_VIEWER_URL=http://viewer-backend:8080 \
  -e HOSPITAL_AGENT_AGENT_ID=2 \
  -v hospital-agent-state:/data \
  hospital-agent:dev
```

Mock-режим разрешён только для `environment=test`. В `prod` агент завершит команду ошибкой конфигурации.

Для проверки разных результатов в `user_requests.payload` можно передать:

- `"mock_outcome": "success"` — успешное выполнение;
- `"mock_outcome": "retryable_error"` — временная ошибка и retry;
- `"mock_outcome": "validation_error"` — невосстановимая ошибка;
- `mock_uploaded_files`, `mock_uploaded_bytes` — значения результата передачи;
- `mock_planned_count`, `mock_emergency_count` — значения тестового отчёта.

## Реальный режим

Для реального больничного запуска не задавайте `HOSPITAL_AGENT_MOCK_COMMANDS`. Смонтируйте конфигурацию и постоянное состояние:

```bash
docker run --rm \
  --name hospital-agent \
  -e HOSPITAL_AGENT_CONFIG=/config/agent_config.json \
  -v /secure/config:/config:ro \
  -v hospital-agent-state:/data \
  hospital-agent:TAG
```

При необходимости можно переопределить:

- `HOSPITAL_AGENT_VIEWER_URL`;
- `HOSPITAL_AGENT_AGENT_ID`;
- `HOSPITAL_AGENT_ENVIRONMENT`;
- `HOSPITAL_AGENT_CONFIG`.

## Публикация в registry

```bash
docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/OWNER/hospital-agent:TAG \
  --push .
```

Для multi-architecture замените platform на `linux/amd64,linux/arm64`.

Образ работает не от root, UID/GID `10001`. Конфигурация по умолчанию предназначена только для E2E и хранит состояние в `/data`.
