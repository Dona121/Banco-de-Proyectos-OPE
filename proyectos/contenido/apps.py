from django.apps import AppConfig


def _validar_antes_de_guardar(sender, instance, **kwargs):
    """Ejecuta ``full_clean()`` antes de cada guardado del ORM.

    Por qué hace falta: `Actividades` define un `clean()` (la fecha programada no
    puede ser posterior al vencimiento) pero **no sobrescribe `save()`**, así que
    esa regla solo corría cuando pasaba por un `ModelForm`. Guardar desde un
    servicio, un comando o el shell la saltaba sin decir nada. Otros modelos del
    mismo archivo (`ActividadEntrega`, `Revisiones`) sí llaman a `full_clean()`:
    la diferencia era un descuido, no una decisión.

    Por qué va aquí y no en el modelo: `contenido/models.py` es definitivo y lo
    edita el usuario. `apps.py` no lo es, y una señal consigue lo mismo.

    Hasta dónde llega: cubre todo lo que pase por el ORM (servicios, admin,
    shell, comandos). **No** cubre `bulk_create`, `queryset.update()` ni el SQL
    crudo, que no emiten esta señal. Para lo que tiene que ser cierto SIEMPRE
    están además las restricciones `CHECK` de la migración
    `contenido/migrations/0004_restricciones_de_integridad.py`.
    """
    instance.full_clean()


class ContenidoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contenido'

    def ready(self):
        from django.db.models.signals import pre_save

        from .models import Actividades

        # El receptor va al nivel del módulo a propósito: Django conecta con
        # referencia débil, así que una función definida aquí dentro se
        # recolectaría al salir de `ready()` y la señal dejaría de llegar en
        # silencio.
        pre_save.connect(
            _validar_antes_de_guardar,
            sender=Actividades,
            dispatch_uid="contenido.validar_actividades",
        )

        # Al borrar una fila con archivo (también en cascada: borrar un proyecto
        # arrastra actividades, entregas y documentos), el objeto del bucket se
        # iba quedando ahí, ocupando sitio y accesible con una URL firmada.
        from cuentas.borrado import conectar_limpieza_de_archivos

        from .models import Documentos

        conectar_limpieza_de_archivos(Documentos)
