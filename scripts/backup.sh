#!/bin/sh
# Ночной бэкап Postgres для РиелторБота.
#
# Работает в отдельном контейнере на образе postgres:16 (версия pg_dump совпадает
# с сервером). Подключение берётся из PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE.
#
#   sh backup.sh          — цикл: раз в сутки после BACKUP_HOUR делает бэкап
#   sh backup.sh --once   — один бэкап прямо сейчас (для проверки и ручного запуска)
#
# Каждый бэкап проверяется (pg_restore --list), неудачный не сохраняется.
# Ошибка записывается в last_error.txt — бот увидит её и сообщит админам.
set -u

DIR="${BACKUP_DIR:-/backups}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
HOUR="${BACKUP_HOUR:-3}"
mkdir -p "$DIR"

fail() {
    echo "$(date '+%Y-%m-%d %H:%M') $1" > "$DIR/last_error.txt"
    echo "backup FAILED: $1" >&2
    rm -f "$DIR"/.realtor-*.part
    return 1
}

do_backup() {
    ts=$(date +%Y-%m-%d_%H%M)
    tmp="$DIR/.realtor-$ts.part"
    out="$DIR/realtor-$ts.dump"

    if ! pg_dump -Fc --no-owner --no-privileges -f "$tmp" 2>"$DIR/.pg_dump.err"; then
        fail "pg_dump: $(tail -n 3 "$DIR/.pg_dump.err" | tr '\n' ' ')"
        return 1
    fi
    if ! pg_restore --list "$tmp" >/dev/null 2>&1; then
        fail "файл бэкапа не прошёл проверку pg_restore --list"
        return 1
    fi
    mv "$tmp" "$out"
    rm -f "$DIR/last_error.txt" "$DIR/.pg_dump.err"
    # Ротация: удаляем бэкапы старше KEEP дней
    find "$DIR" -maxdepth 1 -name 'realtor-*.dump' -type f -mtime +"$KEEP" -delete
    echo "backup ok: $out ($(wc -c < "$out") bytes)"
}

if [ "${1:-}" = "--once" ]; then
    do_backup
    exit $?
fi

echo "backup loop: каждый день после ${HOUR}:00, хранение ${KEEP} дней, папка ${DIR}"
while true; do
    today=$(date +%Y-%m-%d)
    hour=$(date +%H)          # "08" → 8 (без ведущего нуля, иначе sh спотыкается)
    if [ "${hour#0}" -ge "$HOUR" ]; then
        if ! ls "$DIR"/realtor-"$today"_*.dump >/dev/null 2>&1; then
            do_backup || true
        fi
    fi
    sleep 600
done
