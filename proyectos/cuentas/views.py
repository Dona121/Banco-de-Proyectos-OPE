"""Vistas de autenticación y perfil."""
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from contenido.models import Actividades, ActividadEntrega, Proyectos, Revisiones

from .forms import CambiarPasswordForm, LoginForm

User = get_user_model()


class AppLoginView(LoginView):
    template_name = "cuentas/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class AppLogoutView(LogoutView):
    pass


class CambiarPasswordView(SuccessMessageMixin, PasswordChangeView):
    """Cambio de contraseña del usuario autenticado. ``PasswordChangeView`` ya
    exige login y mantiene la sesión activa tras el cambio."""

    template_name = "cuentas/cambiar_password.html"
    form_class = CambiarPasswordForm
    success_url = reverse_lazy("cuentas:perfil")
    success_message = "Tu contraseña se actualizó correctamente."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["breadcrumbs"] = [
            ("Mi perfil", reverse_lazy("cuentas:perfil")),
            ("Cambiar contraseña", None),
        ]
        return ctx


class PerfilView(LoginRequiredMixin, TemplateView):
    """Información básica del usuario y su actividad reciente."""

    template_name = "cuentas/perfil.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["proyectos_relacionados"] = (
            Proyectos.objects.filter(Q(creador_por=user) | Q(asignado_a=user)).count()
        )
        ctx["actividades_relacionadas"] = (
            Actividades.objects.filter(
                Q(asignado_por=user) | Q(asignado_a=user)
            ).count()
        )
        ctx["entregas_recientes"] = (
            ActividadEntrega.objects.filter(usuario=user)
            .select_related("actividad", "actividad__proyecto")
            .order_by("-fecha_creacion")[:5]
        )
        ctx["revisiones_recientes"] = (
            Revisiones.objects.filter(revisor=user)
            .select_related("actividad_entrega__actividad")
            .order_by("-fecha_creacion")[:5]
        )
        return ctx
