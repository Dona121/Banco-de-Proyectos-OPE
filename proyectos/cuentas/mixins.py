"""Mixins de control de acceso por rol para vistas basadas en clase."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .roles import DIRECTOR, COORDINADOR, FORMULADOR, tiene_rol


class RolRequeridoMixin(LoginRequiredMixin):
    """Exige que el usuario pertenezca a alguno de ``roles_permitidos``.

    El superusuario siempre pasa. Defínelo en la vista::

        class MiVista(RolRequeridoMixin, ListView):
            roles_permitidos = (DIRECTOR, COORDINADOR)
    """

    roles_permitidos = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.is_superuser or tiene_rol(request.user, *self.roles_permitidos):
            return super().dispatch(request, *args, **kwargs)
        raise PermissionDenied("No tienes permiso para acceder a esta sección.")


class DirectorRequeridoMixin(RolRequeridoMixin):
    roles_permitidos = (DIRECTOR,)


class CoordinadorRequeridoMixin(RolRequeridoMixin):
    roles_permitidos = (COORDINADOR,)


class FormuladorRequeridoMixin(RolRequeridoMixin):
    roles_permitidos = (FORMULADOR,)


class GestionRequeridoMixin(RolRequeridoMixin):
    """Director o Coordinador (perfiles de gestión)."""

    roles_permitidos = (DIRECTOR, COORDINADOR)
