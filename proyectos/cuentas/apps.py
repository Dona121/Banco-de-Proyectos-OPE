from django.apps import AppConfig


def _limpiar_cache_de_roles(instance, **kwargs):
    """Olvida los roles cacheados por ``roles.roles_de`` cuando cambian.

    Sin esto, cambiar los grupos de un usuario ya consultado en la misma
    petición (o en la misma prueba) seguiría viendo los roles viejos.
    """
    if hasattr(instance, "_roles_cache"):
        del instance._roles_cache


class CuentasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cuentas"
    verbose_name = "Cuentas y accesos"

    def ready(self):
        from django.contrib.auth.models import User
        from django.db.models.signals import m2m_changed

        m2m_changed.connect(
            _limpiar_cache_de_roles,
            sender=User.groups.through,
            dispatch_uid="cuentas.limpiar_cache_de_roles",
        )
