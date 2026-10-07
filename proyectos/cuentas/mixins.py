"""Mixins de control de acceso por rol para vistas basadas en clase."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .roles import DIRECTOR, COORDINADOR, FORMULADOR, ROLES_MODULO, tiene_rol


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


class ModuloProyectosRequeridoMixin(RolRequeridoMixin):
    """Cualquier rol del dominio Proyectos, más el transversal de solo lectura
    ``Consulta``.

    Es el equivalente de ``cuentas_de_cobro.mixins.ModuloRequeridoMixin``: lo
    llevan las vistas de lectura del dominio, mientras las de acción mantienen
    además su mixin de rol específico. Sin él, un rol de cuentas de cobro recibía
    200 en las vistas de Proyectos (sin ver datos, porque los selectores le
    devuelven vacío, pero rompiendo la regla de aislamiento entre dominios).

    El panel de inicio es la excepción: es la pantalla de entrada común (destino
    del login de todos los roles) y por eso no lo lleva.
    """

    roles_permitidos = ROLES_MODULO
