# AI-avatar_command — установка

Реальный стек репозитория: Python-бэкенд (`backend/`, embed + agent сервисы) и
React/Vite-фронтенд в корне. Прежний `setup.sh` ссылался на несуществующий
Node-бэкенд и был удалён.

## Бэкенд (Python)
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # заполни ключи (DeepSeek и пр.) — .env в git не попадает
# запуск сервисов см. в backend/README.md
```

## Фронтенд (Vite)
```bash
npm install
npm run dev                 # http://localhost:5173
```

## Переменные окружения
Все ключи — только в `.env` (он в `.gitignore`). Никогда не коммить ключи в код.
