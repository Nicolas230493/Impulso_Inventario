#!/bin/sh
set -e
# Espera a que PostgreSQL acepte conexiones
python - <<'PY'
import os, time
import psycopg
url = os.environ["DATABASE_URL"]
for i in range(60):
    try:
        psycopg.connect(url).close()
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("PostgreSQL no responde")
PY
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput >/dev/null
fi
exec "$@"
