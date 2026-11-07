#!/usr/bin/env bash
# Single entrypoint, one process supervisor per role (12-factor style).
set -euo pipefail

role="${1:-${SERVICE_ROLE:-web}}"
cd /app

wait_for_mysql() {
  local tries=0
  until python -c "
import os, pymysql
pymysql.connect(host=os.environ.get('MYSQL_HOST','mysql'),
                port=int(os.environ.get('MYSQL_PORT','3306')),
                user=os.environ.get('MYSQL_USER','ticketforge'),
                password=os.environ.get('MYSQL_PASSWORD','ticketforge'))
" 2>/dev/null; do
    tries=$((tries + 1))
    [ "$tries" -ge 60 ] && { echo "MySQL never became reachable" >&2; exit 1; }
    sleep 1
  done
}

case "$role" in
  migrate)
    wait_for_mysql
    python manage.py migrate --noinput
    alembic -c api/alembic.ini upgrade head
    ;;
  web)
    python manage.py collectstatic --noinput
    exec gunicorn web.config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout 60 \
      --access-logfile - \
      --error-logfile -
    ;;
  api)
    exec uvicorn api.app.main:app \
      --host 0.0.0.0 --port 8001 \
      --workers "${UVICORN_WORKERS:-2}" \
      --proxy-headers --forwarded-allow-ips='*'
    ;;
  realtime)
    exec daphne -b 0.0.0.0 -p 8002 web.config.asgi:application
    ;;
  worker)
    exec celery -A worker.celery_app worker \
      --loglevel="${CELERY_LOGLEVEL:-INFO}" \
      --concurrency="${CELERY_CONCURRENCY:-4}" \
      -Q default,emails,search,payments
    ;;
  beat)
    exec celery -A worker.celery_app beat --loglevel="${CELERY_LOGLEVEL:-INFO}"
    ;;
  *)
    exec "$@"
    ;;
esac
