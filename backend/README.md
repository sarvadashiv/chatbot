# chatbot

## Render production setup

To run Telegram in production without a separate bot deployment, keep the backend as the only Render web service and enable Telegram webhook mode.

Required environment variables on Render:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_ENABLED=true`
- `TELEGRAM_WEBHOOK_URL=https://<your-render-domain>/telegram/webhook`
- `TELEGRAM_WEBHOOK_SECRET=<long-random-secret>`
- `GEMINI_API_KEY`
- `BACKEND_API_KEY`
- `REDIS_HOST`
- `REDIS_PORT`

Optional environment variables:

- `TELEGRAM_WEBHOOK_PATH=/telegram/webhook`
- `TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES=false`

Local polling still works with:

```powershell
python bot/telegram_bot.py
```

In production, the backend registers the webhook on startup and receives Telegram updates at `/telegram/webhook`.
