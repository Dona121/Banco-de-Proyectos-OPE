"""Mixins de control de acceso por rol para el módulo de cuentas de cobro.

Reutilizan la base ``RolRequeridoMixin`` de la app ``cuentas`` (login + 403 por
rol), parametrizándola con los grupos propios del módulo.
"""
from cuentas.mixins import RolRequeridoMixin

from .roles import CONTRATISTA, RADICACION, REVISOR, SECOP, SUPERVISOR


class ContratistaRequeridoMixin(RolRequeridoMixin):
    roles_permitidos = (CONTRATISTA,)


class SupervisorRequeridoMixin(RolRequeridoMixin):
    roles_permitidos = (SUPERVISOR,)


class RevisorRequeridoMixin(RolRequeridoMixin):
    roles_permitidos = (REVISOR,)


class RadicacionRequeridoMixin(RolRequeridoMixin):
    roles_permitidos = (RADICACION,)


class SecopRequeridoMixin(RolRequeridoMixin):
    roles_permitidos = (SECOP,)


class AprobacionRadicacionRequeridoMixin(RolRequeridoMixin):
    """Aprobación de la radicación: supervisor o rol de radicación."""

    roles_permitidos = (SUPERVISOR, RADICACION)


class MarcadoDocumentosRequeridoMixin(RolRequeridoMixin):
    """Marcado de estado de documentos (AP/RE/NA): supervisor/radicación durante
    la radicación, o el revisor en turno durante la revisión secuencial."""

    roles_permitidos = (SUPERVISOR, RADICACION, REVISOR)


class TramiteFinalRequeridoMixin(RolRequeridoMixin):
    """Trámites finales EC/SF/SC: rol de radicación, revisor administrativo o
    rol de secop."""

    roles_permitidos = (RADICACION, SECOP, REVISOR)


class ModuloRequeridoMixin(RolRequeridoMixin):
    """Cualquier actor del módulo (para vistas de consulta compartidas)."""

    roles_permitidos = (CONTRATISTA, SUPERVISOR, REVISOR, RADICACION, SECOP)
