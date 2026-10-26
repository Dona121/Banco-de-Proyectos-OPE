"""Vistas del módulo de cuentas de cobro (MVT, delgadas → servicios)."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView

from cuentas.subidas import confirmar_subida, firmar_subida, url_de_descarga
from cuentas.validadores import validar_extension

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
    RadicacionRequeridoMixin,
    RevisorRequeridoMixin,
    SupervisorRequeridoMixin,
    TramiteFinalRequeridoMixin,
)
from django.contrib.contenttypes.models import ContentType

from cuentas.models import AdjuntoDeRevision

from .models import (
    AsignacionRevisor,
    CuentaEntrega,
    DocumentoEntrega,
    DocumentosCuentaCobro,
    RequisitoDocumental,
    TramiteFinal,
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

    def _numero_de(self, nombre, admitidos=None):
        """Un parámetro numérico de la querystring, o "" si no sirve.

        Los tres filtros por id salen de la URL, y filtrar por algo que no es un
        número hace que el ORM lance `ValueError` y la página responda **500**:
        se descarta antes de llegar al queryset. `admitidos` acota además el
        conjunto, que es lo que hace falta para el mes: solo hay doce, y pedir el
        13 debe ser "sin filtro", no una lista vacía sin explicación.
        """
        valor = self.request.GET.get(nombre, "").strip()
        if not valor.isdigit():
            return ""
        if admitidos is not None and int(valor) not in admitidos:
            return ""
        return valor

    def get_queryset(self):
        qs = (
            selectors.cuentas_visibles(self.request.user)
            # El paso actual de cada fila se calcula sobre estas relaciones: sin
            # precargarlas, cada cuenta de la página pagaba una decena de consultas.
            .prefetch_related(*services.RELACIONES_DEL_FLUJO)
            .order_by("-fecha_creacion")
        )
        self.f_contratista = self._numero_de("contratista")
        self.f_vigencia = self._numero_de("vigencia")
        self.f_mes = self._numero_de("mes", admitidos=CuentaEntrega.Meses.values)
        self.f_estado = self.request.GET.get("estado", "").strip()
        if self.f_contratista:
            qs = qs.filter(usuario_id=self.f_contratista)
        if self.f_vigencia:
            qs = qs.filter(vigencia_id=self.f_vigencia)
        if self.f_mes:
            qs = qs.filter(mes=self.f_mes)
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
        # Paso actual / responsable / antigüedad por cuenta de la página. Los
        # tipos obligatorios se resuelven de una vez para todas: cada fila trae su
        # propia instancia de la vigencia y pagaría su consulta por separado.
        services.precargar_tipos_obligatorios(ctx["cuentas"])
        for c in ctx["cuentas"]:
            c.paso = services.paso_actual(c)
        # Opciones de filtro: contratistas presentes en las cuentas visibles.
        visibles = selectors.cuentas_visibles(self.request.user)
        ctx["contratistas"] = (
            visibles.values_list("usuario_id", "usuario__first_name", "usuario__username")
            .distinct().order_by("usuario__first_name")
        )
        # Mismo criterio que con los contratistas: se ofrecen solo las vigencias
        # y los meses que de verdad aparecen en las cuentas visibles, para que
        # ninguna opción del desplegable devuelva una lista vacía.
        ctx["vigencias"] = (
            visibles.values_list("vigencia_id", "vigencia__vigencia")
            .distinct().order_by("-vigencia__vigencia")
        )
        presentes = set(visibles.values_list("mes", flat=True).distinct())
        ctx["meses"] = [
            (valor, etiqueta)
            for valor, etiqueta in CuentaEntrega.Meses.choices
            if valor in presentes
        ]
        ctx["estado_choices"] = CuentaEntrega.ResultadoRevision.choices
        ctx["f_contratista"] = self.f_contratista
        ctx["f_vigencia"] = self.f_vigencia
        ctx["f_mes"] = self.f_mes
        ctx["f_estado"] = self.f_estado
        return ctx


# =========================================================================== #
# Crear cuenta (contratista)
# =========================================================================== #
class CuentaCreateView(ContratistaRequeridoMixin, CreateView):
    template_name = "cuentas_cobro/cuenta_form.html"
    form_class = CuentaForm

    def form_valid(self, form):
        try:
            cuenta = services.crear_cuenta(
                usuario=self.request.user,
                vigencia=form.cleaned_data["vigencia"],
                mes=form.cleaned_data["mes"],
                comentario=form.cleaned_data["comentario"],
            )
        except ValidationError as exc:
            # P. ej. ya existe una cuenta para esa vigencia y mes.
            form.add_error(None, exc)
            return self.form_invalid(form)
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

    # El historial recorre los documentos y las revisiones de TODAS las versiones,
    # cosa que la bandeja no hace. Estas precargas van solo aquí para no encarecer
    # cada fila de la bandeja con datos que allí no se pintan.
    RELACIONES_DEL_HISTORIAL = (
        "documentoentrega_set__documentoscuentacobro_set__tipo_documento",
        "documentoentrega_set__revisioncuentacobro_set__asignacion__revisor",
    )

    def get_queryset(self):
        # El detalle pinta el flujograma completo, la línea de tiempo y el tablero
        # de revisiones: las mismas relaciones que la bandeja, más el historial.
        return selectors.cuentas_visibles(self.request.user).prefetch_related(
            *services.RELACIONES_DEL_FLUJO, *self.RELACIONES_DEL_HISTORIAL
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cuenta = self.object
        user = self.request.user
        entrega = services.ultima_entrega(cuenta)

        ctx["entrega"] = entrega
        documentos = (
            list(entrega.documentoscuentacobro_set.select_related("tipo_documento")
                 .order_by("tipo_documento__nombre"))
            if entrega else []
        )
        # Quitar se decide POR DOCUMENTO, no por cuenta: desde que la devolución
        # arrastra los aprobados, en una misma tabla conviven los que se pueden
        # quitar y los que no. Con una sola bandera, al contratista se le ofrecía
        # el icono sobre un aprobado y recibía un 403 que no dice nada.
        for d in documentos:
            d.puede_eliminar = selectors.puede_eliminar_documento(user, d)
        ctx["documentos"] = documentos
        # Guía para el revisor: no se puede aprobar la revisión mientras haya
        # documentos en PENDIENTE (lo impone RevisionCuentaCobro.clean).
        ctx["hay_docs_pendientes"] = any(
            d.estado == DocumentosCuentaCobro.EstadoDocumento.PENDIENTE for d in documentos
        )
        ctx["entregas"] = cuenta.documentoentrega_set.order_by("-numero_version")
        # Versiones anteriores con sus observaciones: solo lectura, para que el
        # contratista sepa qué documento le objetaron y por qué.
        ctx["historial"] = selectors.historial_de_versiones(cuenta)
        # Soportes que adjuntaron los revisores a la versión vigente. Los ve
        # todo el que ve la cuenta, incluido el contratista: son observaciones
        # dirigidas a él.
        ctx["puede_adjuntar_revision"] = selectors.puede_adjuntar_en_revision(
            user, cuenta
        )
        ctx["adjuntos_revision"] = (
            [
                {
                    "adjunto": a,
                    "puede_quitar": selectors.puede_quitar_adjunto(user, a, cuenta),
                }
                for a in AdjuntoDeRevision.de(entrega)
            ]
            if entrega
            else []
        )
        ctx["tablero"] = selectors.tablero_revisiones(cuenta)
        ctx["faltantes"] = services.documentos_faltantes(cuenta)
        ctx["radicaciones"] = cuenta.revisionpararadicacion_set.order_by("fecha_creacion")
        ctx["docs_cierre"] = (
            cuenta.documentocierre_set.select_related("tipo_documento")
            .order_by("tipo_documento__nombre")
        )
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
        # `puede_cargar` se cierra al entregar; para los textos dirigidos al dueño
        # de la cuenta (p. ej. "ya entregaste, espera la revisión") hace falta
        # saber quién es, no si puede cargar.
        ctx["es_contratista_dueno"] = (
            es_contratista(user) and selectors.es_dueno(user, cuenta)
        )
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
        # Tipos que el contratista todavía puede cargar. Se reutiliza el queryset
        # que ya filtra el formulario (catálogo de la vigencia menos los cargados)
        # para que la subida directa ofrezca exactamente lo mismo.
        ctx["tipos_cargables"] = ctx["doc_form"].fields["tipo_documento"].queryset
        ctx["radicacion_form"] = RevisionRadicacionForm()
        ctx["asignacion_form"] = AsignacionForm()
        ctx["reasignacion_form"] = ReasignacionForm()
        ctx["revision_form"] = RevisionForm()
        ctx["declinar_form"] = DeclinarForm()
        ctx["decision_form"] = DecisionSupervisorForm()
        ctx["cierre_form"] = DocumentoCierreForm(cuenta=cuenta)
        ctx["tramite_form"] = TramiteFinalForm()
        ctx["estado_doc_choices"] = DocumentosCuentaCobro.EstadoDocumento.choices

        ctx["breadcrumbs"] = _crumb_base() + [
            (services.nombre_de_cuenta(cuenta), None)
        ]
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
        # `puede_entregar`, no `puede_cargar_documentos`: además de ser el dueño
        # exige que el paquete esté completo y que la versión no se haya enviado ya.
        _exigir(selectors.puede_entregar(request.user, cuenta))
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


class DocumentoCierreView(RadicacionRequeridoMixin, _AccionCuentaMixin):
    def post(self, request, pk):
        cuenta = self.get_cuenta(request, pk)
        _exigir(selectors.puede_cargar_cierre(request.user, cuenta))
        form = DocumentoCierreForm(request.POST, request.FILES, cuenta=cuenta)
        if form.is_valid():
            try:
                services.cargar_documento_cierre(
                    cuenta, form.cleaned_data["tipo_documento"],
                    form.cleaned_data["documento"], request.user,
                )
                messages.success(request, "Documento de cierre firmado cargado.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Selecciona el tipo y el archivo.")
        return self.volver(cuenta)


# =========================================================================== #
# Subida directa al bucket (con porcentaje de avance)
# =========================================================================== #
# Cada destino dice quién puede subir ahí, en qué carpeta del bucket va el
# archivo y cómo se registra una vez subido. El archivo NO pasa por el servidor:
# ver `cuentas/subidas.py` para el por qué y el flujo completo.
DESTINOS = {
    "entrega": {
        "prefijo": "cuentas_cobro/%Y/%m/",
        "permiso": lambda user, cuenta, datos: selectors.puede_cargar_documentos(
            user, cuenta
        ),
    },
    "cierre": {
        "prefijo": "cierres/%Y/%m/",
        "permiso": lambda user, cuenta, datos: selectors.puede_cargar_cierre(
            user, cuenta
        ),
    },
    # El trámite no lleva tipo de documento: lleva el tipo de trámite (SF/SC),
    # que el navegador manda como campo y queda dentro del token firmado.
    "tramite": {
        "prefijo": "tramites_finales/%Y/%m/",
        "permiso": lambda user, cuenta, datos: selectors.puede_responder_tramite(
            user, cuenta, datos.get("tipo_tramite", "")
        ),
    },
    # Soporte opcional que adjunta quien revisa, junto a su comentario. Cuelga
    # de la versión y no de la revisión, porque se sube ANTES de registrarla.
    "adjunto_revision": {
        "prefijo": "revisiones/%Y/%m/",
        "permiso": lambda user, cuenta, datos: (
            selectors.puede_adjuntar_en_revision(user, cuenta)
        ),
    },
}


def _tipo_de_tramite(valor):
    """Valida el tipo de trámite final (SF/SC) que llega del navegador."""
    if valor not in TramiteFinal.Tipo.values:
        raise ValidationError("Tipo de trámite inválido.")
    return valor


def _tipo_de_documento(cuenta, valor):
    """Valida que el tipo pedido pertenezca al catálogo de la vigencia."""
    if not str(valor).isdigit():
        raise ValidationError("Selecciona el tipo de documento.")
    for tipo in services.tipos_obligatorios(cuenta):
        if tipo.id == int(valor):
            return tipo
    # Los opcionales también valen para la entrega.
    requisito = RequisitoDocumental.objects.filter(
        vigencia=cuenta.vigencia, tipo_documento_id=valor
    ).select_related("tipo_documento").first()
    if requisito is None:
        raise ValidationError("Ese tipo de documento no aplica a esta vigencia.")
    return requisito.tipo_documento


def _mensaje_de_subida(destino, cuenta):
    """Qué decirle al usuario según lo que acaba de pasar.

    "Documento cargado" valía para todos los destinos y se quedaba corto justo
    donde más importa: al responder el último trámite la cuenta **se cierra en
    la misma transacción**, y el usuario no se enteraba. Lo reportó como que el
    paso de SECOP "no terminó la cuenta", cuando sí la había terminado.
    """
    if destino == "adjunto_revision":
        return "Soporte adjuntado a la revisión."
    if destino == "cierre":
        return "Documento de cierre cargado."
    if destino == "tramite":
        cuenta.refresh_from_db()
        if cuenta.fecha_cierre is not None:
            return (
                "Trámite registrado. Con esto se completaron los dos pasos "
                "finales y la cuenta de cobro quedó CERRADA."
            )
        pendientes = [
            TramiteFinal.Tipo(t).label
            for t in services.SECUENCIA_TRAMITES
            if not services.tramite_realizado(cuenta, t)
        ]
        if pendientes:
            return f"Trámite registrado. Falta: {pendientes[0]}."
        return "Trámite registrado."
    return "Documento cargado."


def _adjuntos_de_la_cuenta(cuenta):
    """Adjuntos de revisión de cualquier versión de la cuenta.

    Acota por cuenta a propósito: así un id de otra cuenta responde 404 en vez
    de dejar descargar o borrar algo ajeno.
    """
    return AdjuntoDeRevision.objects.filter(
        content_type=ContentType.objects.get_for_model(DocumentoEntrega),
        object_id__in=cuenta.documentoentrega_set.values_list("pk", flat=True),
    )


class SubidaFirmarView(ModuloRequeridoMixin, _AccionCuentaMixin):
    """Paso 1: comprueba permisos y devuelve una URL firmada para subir."""

    def post(self, request, pk, destino):
        cuenta = self.get_cuenta(request, pk)
        config = DESTINOS.get(destino)
        if config is None:
            raise PermissionDenied("Destino de subida desconocido.")
        _exigir(config["permiso"](request.user, cuenta, request.POST))
        try:
            contexto = {"destino": destino, "cuenta": cuenta.pk}
            if destino == "tramite":
                contexto["tramite"] = _tipo_de_tramite(request.POST.get("tipo_tramite"))
            elif destino == "adjunto_revision":
                # No lleva tipo del catálogo: es un soporte libre del revisor,
                # identificado por el nombre que él escribe.
                pass
            else:
                contexto["tipo"] = _tipo_de_documento(
                    cuenta, request.POST.get("tipo_documento", "")
                ).pk
            archivo = request.POST.get("nombre", "")
            validar_extension(archivo)
            datos = firmar_subida(
                prefijo=config["prefijo"],
                nombre_archivo=archivo,
                content_type=request.POST.get("content_type")
                or "application/octet-stream",
                contexto=contexto,
            )
        except ValidationError as exc:
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
        return JsonResponse(datos)


class SubidaConfirmarView(ModuloRequeridoMixin, _AccionCuentaMixin):
    """Paso 3: verifica el objeto en el bucket y crea el registro."""

    def post(self, request, pk, destino):
        cuenta = self.get_cuenta(request, pk)
        config = DESTINOS.get(destino)
        if config is None:
            raise PermissionDenied("Destino de subida desconocido.")
        _exigir(config["permiso"](request.user, cuenta, request.POST))
        try:
            datos = confirmar_subida(request.POST.get("token", ""))
            # El token lleva la cuenta y el tipo firmados: así nadie puede
            # confirmar en otra cuenta un archivo que subió con este permiso.
            if datos.get("destino") != destino or datos.get("cuenta") != cuenta.pk:
                raise ValidationError("La confirmación no corresponde a esta cuenta.")
            if destino == "entrega":
                services.adjuntar_documento(
                    services.ultima_entrega(cuenta),
                    _tipo_de_documento(cuenta, datos["tipo"]),
                    clave=datos["clave"],
                )
            elif destino == "cierre":
                services.cargar_documento_cierre(
                    cuenta,
                    _tipo_de_documento(cuenta, datos["tipo"]),
                    usuario=request.user,
                    clave=datos["clave"],
                )
            elif destino == "adjunto_revision":
                nombre = (request.POST.get("nombre_documento") or "").strip()
                if not nombre:
                    raise ValidationError("Escribe el nombre del documento.")
                AdjuntoDeRevision.objects.create(
                    content_type=ContentType.objects.get_for_model(DocumentoEntrega),
                    object_id=services.ultima_entrega(cuenta).pk,
                    usuario=request.user, nombre=nombre,
                    archivo=datos["clave"],
                )
            else:
                comentario = (request.POST.get("comentario") or "").strip()
                if not comentario:
                    raise ValidationError("Escribe el detalle de lo realizado.")
                services.responder_tramite(
                    cuenta, datos["tramite"], request.user, True,
                    None, comentario, clave=datos["clave"],
                )
        except ValidationError as exc:
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
        messages.success(request, _mensaje_de_subida(destino, cuenta))
        return JsonResponse({"ok": True})


class EliminarArchivoView(ModuloRequeridoMixin, _AccionCuentaMixin):
    """Quita un documento de la ENTREGA cargado por equivocación, antes de
    entregarla.

    Los documentos de **cierre** no se quitan, aunque antes se podía: completar
    el cierre habilita el primer trámite final, y retirar uno después lo
    deshabilitaba otra vez, dejando la cuenta oscilando entre "lista para
    SIIFWEB" y "faltan documentos" sin que se viera el porqué.

    ⚠️ La contrapartida, que hay que tener presente: `DocumentoCierre` es único
    por (cuenta, tipo), así que tampoco se puede volver a cargar ese tipo. Sin
    borrado, **un cierre mal cargado ya no se corrige desde la aplicación**: hay
    que entrar al admin. Por eso el cargue avisa antes de confirmar. La salida
    limpia sería reemplazar el archivo sin borrar la fila (como la evidencia de
    un trámite), que no descompleta el cierre y por tanto no desestabiliza el
    flujo.

    Las guardas están en los servicios, que es donde se decide; aquí se
    comprueba el permiso del objeto.
    """

    def post(self, request, pk, tipo, doc_pk):
        cuenta = self.get_cuenta(request, pk)
        try:
            if tipo == "documento":
                documento = get_object_or_404(
                    DocumentosCuentaCobro.objects.select_related("tipo_documento"),
                    pk=doc_pk, documento_entrega__cuenta_entrega=cuenta,
                )
                _exigir(selectors.puede_eliminar_documento(request.user, documento))
                nombre = services.eliminar_documento(documento, request.user)
            elif tipo == "adjunto":
                adjunto = get_object_or_404(
                    _adjuntos_de_la_cuenta(cuenta), pk=doc_pk
                )
                _exigir(
                    selectors.puede_quitar_adjunto(request.user, adjunto, cuenta)
                )
                nombre = adjunto.nombre
                adjunto.delete()   # el archivo se va con la fila (post_delete)
            else:
                raise PermissionDenied("Tipo de archivo desconocido.")
            messages.success(request, f"Se quitó «{nombre}». Puedes cargarlo de nuevo.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return self.volver(cuenta)


class DescargaView(ModuloRequeridoMixin, _AccionCuentaMixin):
    """Descarga un archivo de la cuenta con un nombre legible.

    Pasa por el servidor (que redirige al enlace firmado) en vez de poner el
    enlace del bucket en la plantilla: así se comprueba que quien descarga puede
    ver esa cuenta, y el archivo baja con un nombre que dice qué es, no con la
    clave aleatoria con que se guardó.
    """

    def get(self, request, pk, tipo, doc_pk):
        cuenta = self.get_cuenta(request, pk)
        periodo = services.nombre_de_cuenta(cuenta).replace(" · ", " ")
        if tipo == "documento":
            doc = get_object_or_404(
                DocumentosCuentaCobro.objects.select_related("tipo_documento"),
                pk=doc_pk, documento_entrega__cuenta_entrega=cuenta,
            )
            campo, nombre = doc.documento, f"{doc.tipo_documento.nombre} {periodo}"
        elif tipo == "cierre":
            doc = get_object_or_404(
                cuenta.documentocierre_set.select_related("tipo_documento"), pk=doc_pk
            )
            campo = doc.documento
            nombre = f"{doc.tipo_documento.nombre} firmado {periodo}"
        elif tipo == "evidencia":
            tramite = get_object_or_404(cuenta.tramites_finales, pk=doc_pk)
            campo = tramite.evidencia
            nombre = f"Evidencia {tramite.get_tipo_display()} {periodo}"
        elif tipo == "adjunto":
            adjunto = get_object_or_404(
                _adjuntos_de_la_cuenta(cuenta), pk=doc_pk
            )
            campo, nombre = adjunto.archivo, f"{adjunto.nombre} {periodo}"
        else:
            raise PermissionDenied("Tipo de archivo desconocido.")

        extension = (campo.name or "").rsplit(".", 1)
        if len(extension) == 2:
            nombre = f"{nombre}.{extension[1]}"
        try:
            return redirect(url_de_descarga(campo, nombre))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
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
