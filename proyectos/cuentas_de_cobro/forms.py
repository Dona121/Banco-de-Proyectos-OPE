"""Formularios del módulo de cuentas de cobro (estilizados con Tailwind)."""
from django import forms
from django.contrib.auth import get_user_model

from .models import (
    CuentaEntrega,
    DocumentosCuentaCobro,
    RequisitoDocumental,
    RevisionCuentaCobro,
    RevisionParaRadicacion,
    TipoDocumentoCargue,
    TramiteFinal,
)
from .roles import REVISOR

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
            if isinstance(widget, forms.CheckboxInput):
                continue
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 3)
            widget.attrs["class"] = (widget.attrs.get("class", "") + " " + INPUT).strip()


def _revisores():
    return User.objects.filter(groups__name=REVISOR, is_active=True).order_by(
        "first_name", "username"
    )


# --------------------------------------------------------------------------- #
# Contratista
# --------------------------------------------------------------------------- #
class CuentaForm(EstilizadoMixin, forms.ModelForm):
    class Meta:
        model = CuentaEntrega
        fields = ("vigencia", "mes", "comentario")
        widgets = {
            "comentario": forms.Textarea(
                attrs={"placeholder": "Describe la cuenta de cobro de este período…"}
            ),
        }


class DocumentoCuentaForm(EstilizadoMixin, forms.ModelForm):
    class Meta:
        model = DocumentosCuentaCobro
        fields = ("tipo_documento", "documento")

    def __init__(self, *args, cuenta=None, **kwargs):
        super().__init__(*args, **kwargs)
        if cuenta is not None:
            tipos = set(
                RequisitoDocumental.objects.filter(
                    vigencia=cuenta.vigencia
                ).values_list("tipo_documento_id", flat=True)
            )
            # Excluir los tipos ya cargados en la última versión: solo se ofrecen
            # los que aún faltan.
            entrega = cuenta.documentoentrega_set.order_by("-numero_version").first()
            if entrega is not None:
                cargados = set(
                    entrega.documentoscuentacobro_set.values_list(
                        "tipo_documento_id", flat=True
                    )
                )
                tipos -= cargados
            self.fields["tipo_documento"].queryset = (
                self.fields["tipo_documento"].queryset.filter(id__in=tipos)
            )


# --------------------------------------------------------------------------- #
# Supervisor / radicación — radicación
# --------------------------------------------------------------------------- #
class RevisionRadicacionForm(EstilizadoMixin, forms.Form):
    resultado = forms.ChoiceField(
        label="Decisión de radicación",
        choices=RevisionParaRadicacion.ResultadoRevision.choices,
    )
    comentario = forms.CharField(
        label="Comentario",
        widget=forms.Textarea(attrs={"placeholder": "Observaciones de la radicación…"}),
    )


class DocumentoEstadoForm(EstilizadoMixin, forms.Form):
    estado = forms.ChoiceField(
        label="Estado del documento",
        choices=DocumentosCuentaCobro.EstadoDocumento.choices,
    )
    comentario = forms.CharField(
        label="Comentario", required=False,
        widget=forms.Textarea(attrs={"placeholder": "Causal de devolución (si aplica)…"}),
    )


# --------------------------------------------------------------------------- #
# Supervisor — asignación / reasignación
# --------------------------------------------------------------------------- #
class AsignacionForm(EstilizadoMixin, forms.Form):
    rol = forms.ChoiceField(label="Rol", choices=RevisionCuentaCobro.Rol.choices)
    revisor = forms.ModelChoiceField(label="Revisor", queryset=_revisores())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revisor"].queryset = _revisores()


class ReasignacionForm(EstilizadoMixin, forms.Form):
    revisor = forms.ModelChoiceField(label="Nuevo revisor", queryset=_revisores())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revisor"].queryset = _revisores()


# --------------------------------------------------------------------------- #
# Revisor
# --------------------------------------------------------------------------- #
class RevisionForm(EstilizadoMixin, forms.Form):
    resultado = forms.ChoiceField(
        label="Resultado", choices=RevisionCuentaCobro.ResultadoRevision.choices
    )
    comentario = forms.CharField(
        label="Observaciones",
        widget=forms.Textarea(attrs={"placeholder": "Observaciones de la revisión…"}),
    )


class DeclinarForm(EstilizadoMixin, forms.Form):
    motivo = forms.CharField(
        label="Motivo",
        widget=forms.Textarea(attrs={"placeholder": "¿Por qué no puedes revisar?"}),
    )


# --------------------------------------------------------------------------- #
# Supervisor — decisión final
# --------------------------------------------------------------------------- #
class DecisionSupervisorForm(EstilizadoMixin, forms.Form):
    resultado = forms.ChoiceField(
        label="Decisión final", choices=CuentaEntrega.ResultadoRevision.choices
    )
    comentario = forms.CharField(
        label="Comentario", required=False,
        widget=forms.Textarea(attrs={"placeholder": "Razón de la decisión…"}),
    )

    def clean(self):
        cleaned = super().clean()
        resultado = cleaned.get("resultado")
        comentario = (cleaned.get("comentario") or "").strip()
        if resultado == CuentaEntrega.ResultadoRevision.RECHAZADA and not comentario:
            self.add_error("comentario", "El comentario es obligatorio al rechazar.")
        return cleaned


# --------------------------------------------------------------------------- #
# Cierre (lo carga el rol de radicación: mismos tipos del catálogo, firmados)
# --------------------------------------------------------------------------- #
class DocumentoCierreForm(EstilizadoMixin, forms.Form):
    # Form plano (no ModelForm): el clean() de DocumentoCierre accede a
    # cuenta_entrega, que aún no existe durante la validación. El servicio
    # construye la instancia y dispara full_clean con la cuenta ya asignada.
    tipo_documento = forms.ModelChoiceField(
        label="Tipo de documento de cierre",
        queryset=TipoDocumentoCargue.objects.all(),
    )
    documento = forms.FileField(label="Documento firmado")

    def __init__(self, *args, cuenta=None, **kwargs):
        super().__init__(*args, **kwargs)
        if cuenta is not None:
            # Solo los tipos obligatorios de la vigencia que aún no se han
            # cargado como documento de cierre.
            tipos = set(
                RequisitoDocumental.objects.filter(
                    vigencia=cuenta.vigencia, obligatorio=True
                ).values_list("tipo_documento_id", flat=True)
            )
            cargados = set(
                cuenta.documentocierre_set.values_list("tipo_documento_id", flat=True)
            )
            tipos -= cargados
            self.fields["tipo_documento"].queryset = (
                TipoDocumentoCargue.objects.filter(id__in=tipos).order_by("nombre")
            )


# --------------------------------------------------------------------------- #
# Trámites finales (SF / SC)
# --------------------------------------------------------------------------- #
class TramiteFinalForm(EstilizadoMixin, forms.Form):
    # Responder un trámite es marcar "sí" (realizado): exige evidencia y
    # comentario. La guarda de "evidencia exige realizado" la impone el modelo.
    evidencia = forms.FileField(label="Evidencia")
    comentario = forms.CharField(
        label="Comentario",
        widget=forms.Textarea(attrs={"placeholder": "Detalle de lo realizado…"}),
    )
