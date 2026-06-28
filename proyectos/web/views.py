"""Vistas de la aplicación web de negocio (MVT)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from contenido.models import (
    Actividades,
    ActividadEntrega,
    Documentos,
    Proyectos,
    Revisiones,
)
from cuentas.mixins import DirectorRequeridoMixin, GestionRequeridoMixin
from cuentas.roles import (
    COORDINADOR,
    DIRECTOR,
    FORMULADOR,
    es_coordinador,
    es_director,
    es_formulador,
)

from . import metrics, selectors, services
from .reports import excel as report_excel
from .reports import pdf as report_pdf
from .reports import queries as report_queries
from .reports.forms import ReporteAvanceForm, ReporteFormuladosForm
from .forms import (
    ActividadForm,
    DocumentoForm,
    EntregaForm,
    ProyectoForm,
    RevisionForm,
    SubactividadForm,
)

Estado = Actividades.EstadoActividad


# =========================================================================== #
# Dashboard (indicadores por rol)
# =========================================================================== #
class DashboardView(LoginRequiredMixin, TemplateView):
    """Dashboard específico según el rol (Director / Coordinador / Formulador)."""

    def _rol(self):
        user = self.request.user
        # Prioridad: Director > Coordinador > Formulador (superuser = Director).
        if es_director(user):
            return "director"
        if es_coordinador(user):
            return "coordinador"
        if es_formulador(user):
            return "formulador"
        return "generico"

    def get_template_names(self):
        return [f"web/dashboard/{self._rol()}.html"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["breadcrumbs"] = [("Inicio", None)]
        rol = self._rol()
        constructor = {
            "director": metrics.director,
            "coordinador": metrics.coordinador,
            "formulador": metrics.formulador,
        }.get(rol)
        if constructor:
            ctx.update(constructor(self.request.user))
        return ctx


# =========================================================================== #
# Proyectos
# =========================================================================== #
class ProyectoListView(LoginRequiredMixin, ListView):
    template_name = "web/proyectos/lista.html"
    context_object_name = "proyectos"
    paginate_by = 12

    def get_queryset(self):
        qs = selectors.proyectos_visibles(self.request.user)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(nombre__icontains=q)
        return qs.order_by("-fecha_creacion")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["breadcrumbs"] = [("Proyectos", None)]
        return ctx


class ProyectoDetailView(LoginRequiredMixin, DetailView):
    template_name = "web/proyectos/detalle.html"
    context_object_name = "proyecto"

    def get_queryset(self):
        return selectors.proyectos_visibles(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        proyecto = self.object
        ctx["actividades"] = (
            selectors.actividades_visibles(self.request.user)
            .filter(proyecto=proyecto)
            .order_by("fecha_vencimiento")
        )
        ctx["puede_crear_actividad"] = (
            es_coordinador(self.request.user)
            and proyecto.asignado_a_id == self.request.user.id
        ) or self.request.user.is_superuser
        ctx["breadcrumbs"] = [
            ("Proyectos", reverse("web:proyectos")),
            (proyecto.nombre, None),
        ]
        return ctx


class ProyectoCreateView(DirectorRequeridoMixin, CreateView):
    template_name = "web/proyectos/form.html"
    form_class = ProyectoForm

    def form_valid(self, form):
        form.instance.creador_por = self.request.user
        messages.success(self.request, "Proyecto creado correctamente.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("web:proyecto_detalle", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo"] = "Nuevo proyecto"
        ctx["breadcrumbs"] = [
            ("Proyectos", reverse("web:proyectos")),
            ("Nuevo", None),
        ]
        return ctx


class ProyectoUpdateView(DirectorRequeridoMixin, UpdateView):
    template_name = "web/proyectos/form.html"
    form_class = ProyectoForm

    def get_queryset(self):
        # Solo proyectos creados por el director (o superuser).
        return selectors.proyectos_visibles(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Proyecto actualizado.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("web:proyecto_detalle", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo"] = "Editar proyecto"
        ctx["breadcrumbs"] = [
            ("Proyectos", reverse("web:proyectos")),
            (self.object.nombre, reverse("web:proyecto_detalle", args=[self.object.pk])),
            ("Editar", None),
        ]
        return ctx


# =========================================================================== #
# Actividades
# =========================================================================== #
class ActividadListView(LoginRequiredMixin, ListView):
    template_name = "web/actividades/lista.html"
    context_object_name = "actividades"
    paginate_by = 15

    def get_queryset(self):
        qs = selectors.actividades_visibles(self.request.user)
        estado = self.request.GET.get("estado", "")
        q = self.request.GET.get("q", "").strip()
        proyecto = self.request.GET.get("proyecto", "")
        if estado:
            qs = qs.filter(estado=estado)
        if proyecto:
            qs = qs.filter(proyecto_id=proyecto)
        if q:
            qs = qs.filter(Q(nombre__icontains=q) | Q(proyecto__nombre__icontains=q))
        return qs.order_by("fecha_vencimiento")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["estados"] = Estado.choices
        ctx["estado_sel"] = self.request.GET.get("estado", "")
        ctx["q"] = self.request.GET.get("q", "")
        ctx["proyectos"] = selectors.proyectos_visibles(self.request.user)
        ctx["proyecto_sel"] = self.request.GET.get("proyecto", "")
        ctx["ahora"] = timezone.now()
        ctx["breadcrumbs"] = [("Actividades", None)]
        return ctx


class ActividadDetailView(LoginRequiredMixin, DetailView):
    template_name = "web/actividades/detalle.html"
    context_object_name = "actividad"

    def get_queryset(self):
        return selectors.actividades_visibles(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        actividad = self.object
        user = self.request.user
        ctx["subactividades"] = actividad.subactividades_set.order_by("id")
        entregas = (
            actividad.actividadentrega_set
            .select_related("usuario")
            .prefetch_related("documentos_set")
            .order_by("-numero_version")
        )
        ctx["entregas"] = entregas
        # Línea de tiempo combinada (entregas + revisiones).
        eventos = []
        for e in entregas:
            eventos.append({"tipo": "entrega", "obj": e, "fecha": e.fecha_creacion})
            rev = getattr(e, "revisiones", None) if hasattr(e, "revisiones") else None
            if rev:
                eventos.append({"tipo": "revision", "obj": rev, "fecha": rev.fecha_creacion})
        ctx["timeline"] = sorted(eventos, key=lambda x: x["fecha"], reverse=True)
        ctx["puede_entregar"] = selectors.puede_crear_entrega(user, actividad)
        ctx["puede_gestionar"] = (
            es_coordinador(user) and actividad.proyecto.asignado_a_id == user.id
        ) or user.is_superuser
        ctx["subactividad_form"] = SubactividadForm()
        ctx["breadcrumbs"] = [
            ("Proyectos", reverse("web:proyectos")),
            (actividad.proyecto.nombre,
             reverse("web:proyecto_detalle", args=[actividad.proyecto_id])),
            (actividad.nombre, None),
        ]
        return ctx


class ActividadCreateView(GestionRequeridoMixin, CreateView):
    """Crear actividad dentro de un proyecto (coordinador del proyecto)."""

    template_name = "web/actividades/form.html"
    form_class = ActividadForm
    roles_permitidos = (COORDINADOR,)

    def dispatch(self, request, *args, **kwargs):
        self.proyecto = get_object_or_404(
            selectors.proyectos_visibles(request.user), pk=kwargs["proyecto_pk"]
        )
        if not request.user.is_superuser and self.proyecto.asignado_a_id != request.user.id:
            raise PermissionDenied("Solo el coordinador del proyecto crea actividades.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.proyecto = self.proyecto
        form.instance.asignado_por = self.request.user
        form.instance.estado = Estado.PENDIENTE
        try:
            form.instance.full_clean()
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        messages.success(self.request, "Actividad creada y asignada.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("web:actividad_detalle", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo"] = "Nueva actividad"
        ctx["proyecto"] = self.proyecto
        ctx["breadcrumbs"] = [
            ("Proyectos", reverse("web:proyectos")),
            (self.proyecto.nombre,
             reverse("web:proyecto_detalle", args=[self.proyecto.pk])),
            ("Nueva actividad", None),
        ]
        return ctx


class SubactividadCreateView(GestionRequeridoMixin, View):
    """Alta rápida de subactividad desde el detalle de la actividad."""

    roles_permitidos = (COORDINADOR,)

    def post(self, request, actividad_pk):
        actividad = get_object_or_404(
            selectors.actividades_visibles(request.user), pk=actividad_pk
        )
        if not request.user.is_superuser and actividad.proyecto.asignado_a_id != request.user.id:
            raise PermissionDenied()
        form = SubactividadForm(request.POST)
        if form.is_valid():
            sub = form.save(commit=False)
            sub.actividad = actividad
            sub.save()
            messages.success(request, "Subactividad agregada.")
        else:
            messages.error(request, "No se pudo agregar la subactividad.")
        return redirect("web:actividad_detalle", pk=actividad.pk)


# =========================================================================== #
# Entregas
# =========================================================================== #
class EntregaCreateView(LoginRequiredMixin, FormView):
    """El formulador registra una nueva entrega (versión) de su actividad."""

    template_name = "web/entregas/form.html"
    form_class = EntregaForm

    def dispatch(self, request, *args, **kwargs):
        self.actividad = get_object_or_404(
            selectors.actividades_visibles(request.user), pk=kwargs["actividad_pk"]
        )
        if not selectors.puede_crear_entrega(request.user, self.actividad):
            raise PermissionDenied(
                "No puedes registrar entregas para esta actividad."
            )
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            entrega = services.crear_entrega(
                self.actividad, self.request.user, form.cleaned_data["comentario"]
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        messages.success(
            self.request,
            f"Entrega v{entrega.numero_version} registrada. "
            "Ahora puedes adjuntar documentos.",
        )
        return redirect("web:entrega_detalle", pk=entrega.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo"] = "Nueva entrega"
        ctx["actividad"] = self.actividad
        ctx["breadcrumbs"] = [
            ("Actividades", reverse("web:actividades")),
            (self.actividad.nombre,
             reverse("web:actividad_detalle", args=[self.actividad.pk])),
            ("Nueva entrega", None),
        ]
        return ctx


class EntregaDetailView(LoginRequiredMixin, DetailView):
    template_name = "web/entregas/detalle.html"
    context_object_name = "entrega"

    def get_queryset(self):
        return selectors.entregas_visibles(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        entrega = self.object
        user = self.request.user
        ctx["documentos"] = entrega.documentos_set.order_by("nombre")
        ctx["revision"] = getattr(entrega, "revisiones", None) if hasattr(
            entrega, "revisiones"
        ) else None
        ctx["puede_documentar"] = (
            es_formulador(user) and entrega.usuario_id == user.id
            and entrega.actividad.estado != Estado.APROBADA
        ) or user.is_superuser
        ctx["puede_revisar"] = selectors.puede_revisar(user, entrega)
        ctx["documento_form"] = DocumentoForm()
        ctx["revision_form"] = RevisionForm()
        ctx["breadcrumbs"] = [
            ("Actividades", reverse("web:actividades")),
            (entrega.actividad.nombre,
             reverse("web:actividad_detalle", args=[entrega.actividad_id])),
            (f"Entrega v{entrega.numero_version}", None),
        ]
        return ctx


class DocumentoCreateView(LoginRequiredMixin, View):
    """Adjunta un documento a una entrega (formulador dueño de la entrega)."""

    def post(self, request, entrega_pk):
        entrega = get_object_or_404(
            selectors.entregas_visibles(request.user), pk=entrega_pk
        )
        permitido = (
            request.user.is_superuser
            or (es_formulador(request.user) and entrega.usuario_id == request.user.id)
        )
        if not permitido or entrega.actividad.estado == Estado.APROBADA:
            raise PermissionDenied()
        form = DocumentoForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.actividad_entrega = entrega
            doc.save()
            messages.success(request, "Documento adjuntado.")
        else:
            messages.error(request, "Revisa el archivo y el nombre del documento.")
        return redirect("web:entrega_detalle", pk=entrega.pk)


# =========================================================================== #
# Revisiones
# =========================================================================== #
class RevisionCreateView(LoginRequiredMixin, View):
    """El coordinador aprueba o solicita ajustes sobre una entrega."""

    def post(self, request, entrega_pk):
        entrega = get_object_or_404(
            selectors.entregas_visibles(request.user), pk=entrega_pk
        )
        if not selectors.puede_revisar(request.user, entrega):
            raise PermissionDenied("No puedes revisar esta entrega.")
        form = RevisionForm(request.POST)
        if form.is_valid():
            try:
                services.registrar_revision(
                    entrega,
                    request.user,
                    form.cleaned_data["resultado"],
                    form.cleaned_data["comentario"],
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("web:entrega_detalle", pk=entrega.pk)
            messages.success(request, "Revisión registrada.")
        else:
            messages.error(request, "Completa el resultado y las observaciones.")
        return redirect("web:entrega_detalle", pk=entrega.pk)


# =========================================================================== #
# Reportes
# =========================================================================== #
class ReportesIndexView(LoginRequiredMixin, TemplateView):
    """Página de Reportes con los formularios de filtros."""

    template_name = "web/reportes/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_formulados"] = ReporteFormuladosForm(self.request.user)
        ctx["form_avance"] = ReporteAvanceForm(self.request.user)
        ctx["breadcrumbs"] = [("Reportes", None)]
        return ctx


@login_required
def reporte_formulados_excel(request):
    """Genera bajo demanda el .xlsx de Proyectos Formulados."""
    form = ReporteFormuladosForm(request.user, request.GET or None)
    f = form.cleaned_data if form.is_valid() else {}
    filas = report_queries.proyectos_formulados(
        request.user,
        proyecto=f.get("proyecto"), responsable=f.get("responsable"),
        desde=f.get("desde"), hasta=f.get("hasta"),
    )
    contenido = report_excel.proyectos_formulados_xlsx(
        filas,
        generado_por=request.user.get_full_name() or request.user.username,
        generado_en=timezone.localtime(),
    )
    resp = HttpResponse(
        contenido,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = (
        f'attachment; filename="proyectos_formulados_{timezone.now():%Y%m%d}.xlsx"'
    )
    return resp


@login_required
def reporte_avance_pdf(request):
    """Genera bajo demanda el PDF de Avance por Proyecto."""
    form = ReporteAvanceForm(request.user, request.GET or None)
    f = form.cleaned_data if form.is_valid() else {}
    data = report_queries.avance_por_proyecto(
        request.user,
        proyecto=f.get("proyecto"), responsable=f.get("responsable"),
        desde=f.get("desde"), hasta=f.get("hasta"), estados=f.get("estados"),
    )
    contenido = report_pdf.avance_pdf(
        data,
        generado_por=request.user.get_full_name() or request.user.username,
        generado_en=timezone.localtime(),
    )
    resp = HttpResponse(contenido, content_type="application/pdf")
    resp["Content-Disposition"] = (
        f'attachment; filename="avance_por_proyecto_{timezone.now():%Y%m%d}.pdf"'
    )
    return resp
