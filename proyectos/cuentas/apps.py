from django.apps import AppConfig


def _limpiar_cache_de_roles(instance, **kwargs):
    """Olvida los roles cacheados por ``roles.roles_de`` cuando cambian.

    Sin esto, cambiar los grupos de un usuario ya consultado en la misma
    petición (o en la misma prueba) seguiría viendo los roles viejos.
    """
    if hasattr(instance, "_roles_cache"):
        del instance._roles_cache


def _olvidar_extensiones(**kwargs):
    """Invalida la lista de extensiones cacheada al tocarla desde el panel.

    Tiene que vivir al nivel del módulo: Django conecta los receptores con
    referencia débil, así que una función definida dentro de ``ready()`` se
    recolecta en cuanto ``ready()`` termina y la señal deja de llegar, sin aviso.
    """
    from .validadores import limpiar_cache_de_extensiones

    limpiar_cache_de_extensiones()


class CuentasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cuentas"
    verbose_name = "Cuentas y accesos"

    def ready(self):
        from django.contrib.auth.models import User
        from django.db.models.signals import m2m_changed, post_delete, post_save

        m2m_changed.connect(
            _limpiar_cache_de_roles,
            sender=User.groups.through,
            dispatch_uid="cuentas.limpiar_cache_de_roles",
        )

        # La lista de extensiones admitidas se cachea por proceso (se consulta en
        # cada subida); sin esto, activar o desactivar una desde el panel no
        # surtiría efecto hasta reiniciar.
        from .models import ExtensionArchivo

        for nombre, senal in (("save", post_save), ("delete", post_delete)):
            senal.connect(
                _olvidar_extensiones,
                sender=ExtensionArchivo,
                dispatch_uid=f"cuentas.limpiar_cache_de_extensiones.{nombre}",
            )
