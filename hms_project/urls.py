"""
Root URL configuration for hms_project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('housing.urls')),
    path('api/', include('housing.api_urls')),
]

# Serve media files in development (skipped when CLOUDINARY_URL is set)
_media_root = getattr(settings, 'MEDIA_ROOT', None)
if _media_root:
    urlpatterns += static(settings.MEDIA_URL, document_root=_media_root)
