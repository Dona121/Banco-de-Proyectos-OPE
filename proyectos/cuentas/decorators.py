"""Decoradores de control de acceso por rol para vistas basadas en función."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .roles import tiene_rol


def rol_requerido(*roles):
    """Permite el acceso solo a usuarios con alguno de ``roles`` (o superuser)."""

    def decorador(view):
        @wraps(view)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.is_superuser or tiene_rol(request.user, *roles):
                return view(request, *args, **kwargs)
            raise PermissionDenied("No tienes permiso para acceder a esta sección.")

        return _wrapped

    return decorador
