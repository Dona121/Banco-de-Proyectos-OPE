"""Control de acceso por rol para vistas función (las CBV usan ``mixins``)."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .roles import tiene_rol


def rol_requerido(*roles):
    """Exige login y pertenencia a alguno de ``roles``. El superusuario pasa.

    Equivalente a ``RolRequeridoMixin`` para las vistas que son funciones, como
    la generación de reportes::

        @rol_requerido(*ROLES_MODULO)
        def reporte_x(request): ...
    """

    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(request, *args, **kwargs):
            if not (request.user.is_superuser or tiene_rol(request.user, *roles)):
                raise PermissionDenied("No tienes permiso para acceder a esta sección.")
            return vista(request, *args, **kwargs)

        return envoltura

    return decorador
