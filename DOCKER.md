# Docker image

## Сборка

```bash
docker build \
  --build-arg VERSION=dev \
  --build-arg VCS_REF="$(git rev-parse --short HEAD)" \
  -t hospital-agent:dev .
```

Образ не содержит конфигурацию конкретной больницы и секреты. Для запуска
нужно смонтировать `agent_config.json` в `/app/agent_config.json`, а PACS-конфиг
— по пути, указанному в `pacs_config_path`.

Пример:

```bash
docker run --rm \
  --name hospital-agent \
  --network viewer_application \
  -v "$PWD/agent_config.json:/app/agent_config.json:ro" \
  -v "$PWD/config.json:/config/pacs.json:ro" \
  -v hospital-agent-data:/data \
  hospital-agent:dev
```

Для полного локального стека используйте `compose.yaml` из соседнего
репозитория `viewer_backend`:

```bash
cd ../viewer_backend
docker compose up -d --build --wait
```

## Публикация

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/OWNER/hospital-agent:TAG \
  --push .
```

Runtime работает с UID/GID `10001`. Данные агента размещаются в `/data`,
рабочие тестовые файлы — в `/work`. Healthcheck проверяет, что основной
процесс контейнера жив.
