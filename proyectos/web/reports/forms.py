"""Formularios de filtros de los reportes."""
from django import forms
from django.contrib.auth import get_user_model

from contenido.models import Actividades
from cuentas.roles import COORDINADOR, DIRECTOR

from .. import selectors

User = get_user_model()

INPUT = (
    "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 "
    "focus:border-brand focus:ring-2 focus:ring-brand/30 focus:outline-none"
)


class DateInput(forms.DateInput):
    input_type = "date"


class _ReporteBaseForm(forms.Form):
    desde = forms.DateField(required=False, label="Desde", widget=DateInput())
    hasta = forms.DateField(required=False, label="Hasta", widget=DateInput())
    proyecto = forms.ModelChoiceField(
        required=False, queryset=None, label="Proyecto", empty_label="Todos los proyectos"
    )
    responsable = forms.ModelChoiceField(
        required=False, queryset=None, label="Responsable", empty_label="Todos los responsables"
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proyecto"].queryset = selectors.proyectos_visibles(user)
        self.fields["responsable"].queryset = (
            User.objects.filter(groups__name__in=[DIRECTOR, COORDINADOR])
            .distinct().order_by("first_name", "username")
        )
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", INPUT)


class ReporteFormuladosForm(_ReporteBaseForm):
    """Filtros del Reporte de Proyectos Formulados (Excel)."""


class ReporteAvanceForm(_ReporteBaseForm):
    """Filtros del Reporte de Avance por Proyecto (PDF)."""

    estados = forms.MultipleChoiceField(
        required=False, label="Estados de actividad",
        choices=Actividades.EstadoActividad.choices,
        widget=forms.CheckboxSelectMultiple,
    )
