release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn hms_project.wsgi --bind 0.0.0.0:$PORT --workers 2 --log-file -
