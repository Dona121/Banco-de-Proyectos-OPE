"""Vistas del módulo de cuentas de cobro (MVT, delgadas → servicios)."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView

from . import selectors, services
from .forms import (
    AsignacionForm,
    CuentaForm,
    DecisionSupervisorForm,
    DeclinarForm,
    DocumentoCierreForm,
    DocumentoCuentaForm,
    ReasignacionForm,
    RevisionForm,
    RevisionRadicacionForm,
    TramiteFinalForm,
)
from .mixins import (
    AprobacionRadicacionRequeridoMixin,
    ContratistaRequeridoMixin,
    MarcadoDocumentosRequeridoMixin,
    ModuloRequeridoMixin,
    RevisorRequeridoMixin,
    SupervisorRequeridoMixin,
    TramiteFinalRequeridoMixin,
)
from .models import (
    AsignacionRevisor,
    CuentaEntrega,
    DocumentosCuentaCobro,
)
from .roles import es_contratista, es_supervisor


def _crumb_base():
    return [("Cuentas de cobro", reverse("cuentas_cobro:bandeja"))]


# =========================================================================== #
# Bandeja por rol
# =========================================================================== #
class BandejaView(ModuloRequeridoMixin, ListView):
    template_name = "cuentas_cobro/bandeja.html"
    context_object_name = "cuentas"
    paginate_by = 15

    def get_queryset(self):
        qs = selectors.cuentas_visibles(self.request.user).order_by("-fecha_creacion")
        self.f_contratista = self.request.GET.get("contratista", "").strip()
        self.f_estado = self.request.GET.get("estado", "").strip()
        if self.f_contratista:
            qs = qs.filter(usuario_id=self.f_contratista)
        if self.f_estado == "cerrada":
            qs = qs.filter(fecha_cierre__isnull=False)
        elif self.f_estado == "sin_revisar":
            qs = qs.filter(estado_revisores__isnull=True)
        elif self.f_estado:
            qs = qs.filter(estado_revisores=self.f_estado)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["breadcrumbs"] = [("Cuentas de cobro", None)]
        ctx["puede_crear"] = es_contratista(self.request.user)
        # Paso actual / responsable / antigüedad por cuenta de la página.
        for c in ctx["cuentas"]:
            c.paso = services.paso_actual(c)
        # Opciones de filtro: contratistas presentes en las cuentas visibles.
        visibles = selectors.cuentas_visibles(self.request.user)
        ctx["contratistas"] = (
            visibles.values_list("usuario_id", "usuario__first_name", "usuario__username")
            .distinct().order_by("usuario__first_name")
        )
        ctx["estado_choices"] = CuentaEntrega.ResultadoRevision.choices
        ctx["f_contratista"] = self.f_contratista
        ctx["f_estado"] = self.f_estado
        return ctx


# =========================================================================== #
# Crear cuenta (contratista)
# =========================================================================== #
class CuentaCreateView(ContratistaRequeridoMixin, CreateView):
    template_name = "cuentas_cobro/cuenta_form.html"
    form_class = CuentaForm

    def form_valid(self, form):
        cuenta = services.crear_cuenta(
            usuario=self.request.user,
            vigencia=form.cleaned_data["vigencia"],
            mes=form.cleaned_data["mes"],
            comentario=form.cleaned_data["comentario"],
        )
        messages.success(
            self.request, "Cuenta creada. Ahora carga los documentos obligatorios."
        )
        self.object = cuenta
        return redirect("cuentas_cobro:cuenta_detalle", pk=cuenta.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo"] = "Nueva cuenta de cobro"
        ctx["breadcrumbs"] = _crumb_base() + [("Nueva", None)]
        return ctx


# =========================================================================== #
# Detalle de cuenta
# =========================================================================== #
class CuentaDetailView(ModuloRequeridoMixin, DetailView):
    template_name = "cuentas_cobro/cuenta_detalle.html"
    context_object_name = "cuenta"

    def get_queryset(self):
        return selectors.cuentas_visibles(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cuenta = self.object
        user = self.request.user
        entrega = services.ultima_entrega(cuenta)

        ctx["entrega"] = entrega
        ctx["documentos"] = (
            entrega.documentoscuentacobro_set.select_related("tipo_documento")
            .order_by("tipo_documento__nombre")
            if entrega else []
        )
        ctx["entregas"] = cuenta.documentoentrega_set.order_by("-numero_version")
        ctx["tablero"] = selectors.tablero_revisiones(cuenta)
        ctx["faltantes"] = services.documentos_faltantes(cuenta)
        ctx["radicaciones"] = cuenta.revisionpararadicacion_set.order_by("fecha_creacion")
        ctx["docs_cierre"] = cuenta.documentocierre_set.order_by("tipo")
        ctx["cierre_faltantes"] = services.documentos_cierre_faltantes(cuenta)

        # Trámites finales (con permiso por fila).
        tramites = selectors.tramites_finales(cuenta)
        for f in tramites:
            f["puede"] = selectors.puede_responder_tramite(user, cuenta, f["tipo"])
        ctx["tramites"] = tramites

        # Línea de tiempo con marca de devolución.
        eventos = list(cuenta.eventos.select_related("actor"))
        for e in eventos:
            e.es_devolucion = services.es_devolucion(e.evento)
        ctx["eventos"] = eventos
        ctx["hay_devoluciones"] = any(e.es_devolucion for e in eventos)

        # Flujo / etapa actual (§10).
        ctx["flujo"] = services.flujo_de_cuenta(cuenta)

        Resultado = CuentaEntrega.ResultadoRevision
        ctx["es_sup"] = es_supervisor(user)
        ctx["puede_cargar"] = selectors.puede_cargar_documentos(user, cuenta)
        ctx["entrega_enviada"] = services.entrega_enviada(cuenta)
        ctx["puede_entregar"] = selectors.puede_entregar(user, cuenta)
        ctx["puede_radicar"] = selectors.puede_radicar(user, cuenta)
        ctx["puede_marcar_docs"] = selectors.puede_marcar_documentos(user, cuenta)
        ctx["puede_asignar"] = es_supervisor(user) and cuenta.fecha_radicacion is not None
        ctx["puede_decidir"] = (
            es_supervisor(user)
            and cuenta.estado_revisores == Resultado.APROBADA
            and cuenta.estado_supervisor is None
        )
        ctx["puede_cargar_cierre"] = selectors.puede_cargar_cierre(user, cuenta)
        mis = []
        for a in selectors.asignaciones_activas_de(user, cuenta):
            mis.append({"asignacion": a, "puede": selectors.puede_revisar(user, a)})
        ctx["mis_asignaciones"] = mis

        ctx["doc_form"] = DocumentoCuentaForm(cuenta=cuenta)
        ctx["radicacion_form"] = RevisionRadicacionForm()
        ctx["asignacion_form"] = AsignacionForm()
        ctx["reasignacion_form"] = ReasignacionForm()
        ctx["revision_form"] = RevisionForm()
        ctx["declinar_form"] = DeclinarForm()
        ctx["decision_form"] = DecisionSupervisorForm()
        ctx["cierre_form"] = DocumentoCierreForm()
        ctx["tramite_form"] = TramiteFinalForm()
        ctx["estado_doc_choices"] = DocumentosCuentaCobro.EstadoDocumento.choices

        ctx["breadcrumbs"] = _crumb_base() + [(str(cuenta), None)]
        return ctx


# =========================================================================== #
# Helpers de acción
# =========================================================================== #
class _AccionCuentaMixin(LoginRequiredMixin, View):
    """Base para vistas de acción POST sobre una cuenta visible."""

    def get_cuenta(self, request, pk):
        return get_object_or_404(selectors.cuentas_visibles(request.user), pk=pk)

    def volver(self, cuenta):
        return redirect("cuentas_cobro:cuenta_detalle", pk=cuenta.pk)


def _exigir(condicion, mensaje="No tienes permiso para esta acción."):
    if not condicion:
        raise PermissionDenied(mensaje)


# =========================================================================== #
# Contratista: cargar documento y entregar
# =========================================================================== #
class DocumentoCargarView(ContratistaRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, pk):
        cuenta = self.get_cuenta(request, pk)
        _exigir(selectors.puede_cargar_documentos(request.user, cuenta))
        entrega = services.ultima_entrega(cuenta)
        if entrega is None:
            messages.error(request, "La cuenta no tiene una versión activa.")
            return self.volver(cuenta)
        form = DocumentoCuentaForm(request.POST, request.FILES, cuenta=cuenta)
        if form.is_valid():
            try:
                services.adjuntar_documento(
                    entrega,
                    form.cleaned_data["tipo_documento"],
                    form.cleaned_data["documento"],
                )
                messages.success(request, "Documento cargado.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Revisa el tipo y el archivo del documento.")
        return self.volver(cuenta)


class EntregarView(ContratistaRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, pk):
        cuenta = self.get_cuenta(request, pk)
        _exigir(selectors.puede_cargar_documentos(request.user, cuenta))
        try:
            services.entregar(cuenta, request.user)
            messages.success(request, "Documentos enviados a revisión.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return self.volver(cuenta)


# =========================================================================== #
# Radicación (supervisor o rol de radicación) y revisión por documento
# =========================================================================== #
class RevisionRadicacionView(AprobacionRadicacionRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, pk):
        cuenta = self.get_cuenta(request, pk)
        _exigir(selectors.puede_radicar(request.user, cuenta))
        form = RevisionRadicacionForm(request.POST)
        if form.is_valid():
            try:
                services.registrar_revision_radicacion(
                    cuenta, request.user,
                    form.cleaned_data["resultado"], form.cleaned_data["comentario"],
                )
                messages.success(request, "Decisión de radicación registrada.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Completa la decisión y el comentario.")
        return self.volver(cuenta)


class DocumentoRevisarView(MarcadoDocumentosRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, doc_pk):
        documento = get_object_or_404(
            DocumentosCuentaCobro.objects.select_related(
                "documento_entrega__cuenta_entrega", "tipo_documento"
            ),
            pk=doc_pk,
        )
        cuenta = documento.documento_entrega.cuenta_entrega
        get_object_or_404(selectors.cuentas_visibles(request.user), pk=cuenta.pk)
        _exigir(selectors.puede_marcar_documentos(request.user, cuenta))
        # Solo se marca sobre la última entrega.
        if documento.documento_entrega_id != services.ultima_entrega(cuenta).pk:
            messages.error(request, "Solo se pueden marcar documentos de la última versión.")
            return self.volver(cuenta)
        try:
            services.revisar_documento(
                documento,
                request.POST.get("estado", ""),
                request.POST.get("comentario", ""),
                actor=request.user,
            )
            messages.success(request, "Documento actualizado.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return self.volver(cuenta)


# =========================================================================== #
# Supervisor: asignación y reasignación
# =========================================================================== #
class AsignarRevisorView(SupervisorRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, pk):
        cuenta = self.get_cuenta(request, pk)
        _exigir(es_supervisor(request.user))
        form = AsignacionForm(request.POST)
        if form.is_valid():
            try:
                services.asignar_revisor(
                    cuenta, form.cleaned_data["rol"],
                    form.cleaned_data["revisor"], request.user,
                )
                messages.success(request, "Revisor asignado.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Selecciona rol y revisor.")
        return self.volver(cuenta)


class ReasignarView(SupervisorRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, asignacion_pk):
        asignacion = get_object_or_404(
            AsignacionRevisor.objects.select_related("cuenta_entrega"), pk=asignacion_pk
        )
        cuenta = asignacion.cuenta_entrega
        _exigir(es_supervisor(request.user))
        get_object_or_404(selectors.cuentas_visibles(request.user), pk=cuenta.pk)
        form = ReasignacionForm(request.POST)
        if form.is_valid():
            try:
                services.reasignar(
                    cuenta, asignacion.rol, form.cleaned_data["revisor"], request.user
                )
                messages.success(request, "Rol reasignado.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Selecciona el nuevo revisor.")
        return self.volver(cuenta)


# =========================================================================== #
# Revisor: declinar y revisar
# =========================================================================== #
class DeclinarView(RevisorRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, asignacion_pk):
        asignacion = get_object_or_404(
            AsignacionRevisor.objects.select_related("cuenta_entrega"), pk=asignacion_pk
        )
        cuenta = asignacion.cuenta_entrega
        _exigir(request.user.is_superuser or asignacion.revisor_id == request.user.id)
        form = DeclinarForm(request.POST)
        if form.is_valid():
            try:
                services.declinar_asignacion(asignacion, form.cleaned_data["motivo"])
                messages.success(request, "Declinaste la asignación.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Indica el motivo.")
        return self.volver(cuenta)


class RevisionView(RevisorRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, asignacion_pk):
        asignacion = get_object_or_404(
            AsignacionRevisor.objects.select_related("cuenta_entrega"), pk=asignacion_pk
        )
        cuenta = asignacion.cuenta_entrega
        _exigir(selectors.puede_revisar(request.user, asignacion))
        form = RevisionForm(request.POST)
        if form.is_valid():
            try:
                services.registrar_revision(
                    asignacion,
                    form.cleaned_data["resultado"], form.cleaned_data["comentario"],
                )
                messages.success(request, "Revisión registrada.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Completa el resultado y las observaciones.")
        return self.volver(cuenta)


# =========================================================================== #
# Supervisor: decisión final · Contratista: cierre · Trámites finales
# =========================================================================== #
class DecisionSupervisorView(SupervisorRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, pk):
        cuenta = self.get_cuenta(request, pk)
        _exigir(es_supervisor(request.user))
        form = DecisionSupervisorForm(request.POST)
        if form.is_valid():
            try:
                services.decidir_supervisor(
                    cuenta, request.user,
                    form.cleaned_data["resultado"], form.cleaned_data["comentario"],
                )
                messages.success(request, "Decisión del supervisor registrada.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            for err in form.errors.values():
                messages.error(request, "; ".join(err))
        return self.volver(cuenta)


class DocumentoCierreView(ContratistaRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, pk):
        cuenta = self.get_cuenta(request, pk)
        _exigir(selectors.puede_cargar_cierre(request.user, cuenta))
        form = DocumentoCierreForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                services.cargar_documento_cierre(
                    cuenta, form.cleaned_data["tipo"],
                    form.cleaned_data["documento"], request.user,
                )
                messages.success(request, "Documento de cierre cargado.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Selecciona el tipo y el archivo.")
        return self.volver(cuenta)


class TramiteFinalView(TramiteFinalRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, pk, tipo):
        cuenta = self.get_cuenta(request, pk)
        _exigir(selectors.puede_responder_tramite(request.user, cuenta, tipo))
        form = TramiteFinalForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                services.responder_tramite(
                    cuenta, tipo, request.user, True,
                    form.cleaned_data["evidencia"], form.cleaned_data["comentario"],
                )
                messages.success(request, "Trámite registrado.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Adjunta la evidencia y el comentario.")
        return self.volver(cuenta)
