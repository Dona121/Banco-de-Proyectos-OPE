"""Formularios de la app web, con estilos de marca (Tailwind)."""
from django import forms
from django.contrib.auth import get_user_model

from contenido.models import (
    Actividades,
    ActividadEntrega,
    Documentos,
    Proyectos,
    Revisiones,
    Subactividades,
)
from cuentas.roles import COORDINADOR, FORMULADOR

User = get_user_model()

INPUT = (
    "w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm text-slate-800 "
    "placeholder-slate-400 focus:border-brand focus:ring-2 focus:ring-brand/30 "
    "focus:outline-none transition bg-white"
)


class EstilizadoMixin:
    """Aplica clases de Tailwind a todos los widgets del formulario."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                continue
            css = INPUT
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 3)
            widget.attrs["class"] = (widget.attrs.get("class", "") + " " + css).strip()


def _usuarios_de(grupo):
    return User.objects.filter(groups__name=grupo, is_active=True).order_by(
        "first_name", "username"
    )


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        kwargs.setdefault("format", "%Y-%m-%dT%H:%M")
        super().__init__(**kwargs)


class ProyectoForm(EstilizadoMixin, forms.ModelForm):
    class Meta:
        model = Proyectos
        fields = ("nombre", "asignado_a")
        widgets = {"nombre": forms.TextInput()}
        labels = {"asignado_a": "Coordinador asignado"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asignado_a"].queryset = _usuarios_de(COORDINADOR)


class ActividadForm(EstilizadoMixin, forms.ModelForm):
    class Meta:
        model = Actividades
        fields = ("nombre", "fecha_programada", "fecha_vencimiento", "asignado_a")
        widgets = {
            "nombre": forms.TextInput(),
            "fecha_programada": DateTimeLocalInput(),
            "fecha_vencimiento": DateTimeLocalInput(),
        }
        labels = {"asignado_a": "Formulador asignado"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asignado_a"].queryset = _usuarios_de(FORMULADOR)
        for nombre in ("fecha_programada", "fecha_vencimiento"):
            self.fields[nombre].input_formats = ["%Y-%m-%dT%H:%M"]


class SubactividadForm(EstilizadoMixin, forms.ModelForm):
    class Meta:
        model = Subactividades
        fields = ("nombre",)
        widgets = {"nombre": forms.TextInput(attrs={"placeholder": "Nueva subactividad"})}


class EntregaForm(EstilizadoMixin, forms.Form):
    """Plano a propósito: el servicio crea y valida la ActividadEntrega.

    (Un ModelForm dispararía el ``clean()`` del modelo antes de tener
    asignada la actividad y fallaría.)
    """

    comentario = forms.CharField(
        label="Comentario",
        widget=forms.Textarea(
            attrs={"placeholder": "Describe lo que entregas en esta versión…"}
        ),
    )


class DocumentoForm(EstilizadoMixin, forms.ModelForm):
    class Meta:
        model = Documentos
        fields = ("nombre", "archivo")
        widgets = {"nombre": forms.TextInput()}


class RevisionForm(EstilizadoMixin, forms.Form):
    """Plano: el servicio crea y valida la Revisión y actualiza el estado."""

    resultado = forms.ChoiceField(
        label="Resultado", choices=Revisiones.ResultadoRevision.choices
    )
    comentario = forms.CharField(
        label="Observaciones",
        widget=forms.Textarea(attrs={"placeholder": "Observaciones de la revisión…"}),
    )
