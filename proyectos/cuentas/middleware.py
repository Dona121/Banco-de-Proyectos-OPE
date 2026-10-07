"""Middleware transversal de la plataforma."""
from django.utils.cache import add_never_cache_headers


class SinCacheDeNavegador:
    """Prohíbe al navegador guardarse las páginas de la aplicación.

    Por qué: las respuestas solo llevaban ``Vary: Cookie`` y ninguna
    ``Cache-Control``. Sin una instrucción explícita el navegador decide por su
    cuenta cuánto considerar fresca una página, y al volver con el botón
    "atrás" usa su copia sin preguntar. En una aplicación donde cada pantalla
    muestra datos que cambian y dependen de quién eres, eso enseña información
    vieja como si fuera actual.

    Se detectó con un caso real: un proyecto con cuatro actividades mostraba
    "1 actividad" en la tarjeta. El dato y el código eran correctos (ninguna
    tarjeta de la base podía dar 1 en ese momento); lo que el usuario veía era
    una página de media hora antes, cuando solo existía la primera actividad.

    Dos motivos más, menos visibles pero peores:

    * **Sesión cerrada.** Sin esto, tras salir el botón "atrás" puede devolver
      páginas con datos del usuario anterior, que en un equipo compartido es una
      fuga.
    * **Token CSRF caducado.** Una página de formulario servida desde la caché
      trae un token viejo y el envío responde 403 sin explicación.

    Alcance: solo lo que atraviesa este middleware. Va **después** de WhiteNoise
    en ``MIDDLEWARE``, así que los archivos estáticos no pasan por aquí y
    conservan su caché larga, que es la que hace que la aplicación cargue
    rápido. Y respeta una ``Cache-Control`` que la vista ya haya puesto a
    propósito.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        respuesta = self.get_response(request)
        if not respuesta.has_header("Cache-Control"):
            # `add_never_cache_headers` es el mismo helper que usa el decorador
            # `never_cache` de Django: no-store, no-cache, must-revalidate,
            # private y `Expires` en el pasado.
            add_never_cache_headers(respuesta)
        return respuesta
