#!/bin/sh
# Восстановление базы РиелторБота из бэкапа.
#
#   cd /root/ai-research-demo
#   sh scripts/restore_backup.sh backups/realtor-2026-10-17_0300.dump
#
# Останавливает бота (чтобы никто не писал в базу), заменяет данные данными
# из бэкапа и запускает бота обратно. Перед заменой спрашивает подтверждение.
set -eu

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
    echo "Укажи файл бэкапа. Доступные:"
    ls -1t backups/realtor-*.dump 2>/dev/null | head -n 14 || echo "  (бэкапов нет)"
    exit 1
fi

echo "ВНИМАНИЕ: все текущие данные в базе будут заменены данными из:"
echo "  $FILE"
printf "Продолжить? Напиши yes: "
read -r answer
[ "$answer" = "yes" ] || { echo "Отменено."; exit 1; }

echo "Останавливаю бота…"
docker compose stop bot userbot 2>/dev/null || docker compose stop bot

echo "Восстанавливаю базу…"
ok=1
docker compose exec -T db pg_restore --clean --if-exists --no-owner --no-privileges \
    -U user -d realtor < "$FILE" || ok=0

# Бота запускаем в любом случае — даже если pg_restore на что-то ругнулся
echo "Запускаю бота…"
docker compose start bot
docker compose start userbot 2>/dev/null || true
if [ "$ok" = 1 ]; then
    echo "Готово. Проверь бота командой /start и админкой «Статистика»."
else
    echo "pg_restore сообщил об ошибках (см. вывод выше). Бот запущен — проверь данные."
    exit 1
fi
