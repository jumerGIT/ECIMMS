release: python manage.py collectstatic --noinput
web: gunicorn hms_project.wsgi --log-file -
