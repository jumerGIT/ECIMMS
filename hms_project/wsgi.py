import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hms_project.settings')

_django_app = get_wsgi_application()


def application(environ, start_response):
    # Bypass Django entirely for the health check so no middleware
    # (ALLOWED_HOSTS, sessions, auth) can interfere with it.
    if environ.get('PATH_INFO') == '/health/':
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return [b'OK']
    return _django_app(environ, start_response)
