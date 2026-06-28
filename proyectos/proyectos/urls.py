"""URL configuration for proyectos project."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('cuentas/', include('cuentas.urls')),
    path('cuentas-cobro/', include('cuentas_de_cobro.urls')),
    path('', include('web.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
