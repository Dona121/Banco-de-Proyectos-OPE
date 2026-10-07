"""Settings para correr las pruebas sin tocar nada de producción.

Úsalo SIEMPRE que corras el suite::

    uv run python manage.py test --settings=proyectos.test_settings

Qué resuelve, y por qué no basta con los settings normales:

* **Base de datos.** `DATABASE_URL` apunta a Supabase; con los settings de
  producción, ``manage.py test`` crea y borra una base ``test_postgres`` **en el
  servidor**. Aquí se fija SQLite (el runner de Django la levanta en memoria).
* **Archivos.** El storage por defecto es S3 contra el bucket real, así que
  cualquier prueba que guarde un ``FileField`` **sube el archivo a producción**
  cuando hay credenciales en el entorno (y falla con ``NoCredentialsError``
  cuando no las hay). Aquí se fija ``InMemoryStorage``, de modo que ninguna ruta
  pueda tocar el bucket por accidente, sin tener que repetir un decorador
  ``override_settings`` en cada clase de prueba.
* **Estáticos.** Se usa el storage plano en vez del manifiesto: así el suite no
  depende de haber corrido ``collectstatic`` antes.
* **Arranque sin `.env`.** Las variables se fijan ANTES de importar los settings
  de producción (que abortan si falta ``SECRET_KEY``), para que el suite corra en
  cualquier máquina. ``setdefault`` no pisa lo que ya venga del entorno.
"""
import os

os.environ.setdefault("SECRET_KEY", "clave-solo-para-pruebas-no-usar-en-produccion")
# Sin esto, SECURE_SSL_REDIRECT devuelve 301 a cada petición del cliente de pruebas.
os.environ.setdefault("HTTPS_ESTRICTO", "0")
os.environ.setdefault("SUPABASE_S3_ENDPOINT_URL", "http://localhost:9000")

from .settings import *  # noqa: E402,F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

# El suite tarda ~110 s con el hasher real, y casi todo es hashear contraseñas.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Que el ruido de logs no tape la salida del runner.
LOGGING["loggers"]["django"]["level"] = "ERROR"  # noqa: F405
