"""Formularios de cuentas (login con estilos de marca)."""
from django.contrib.auth.forms import AuthenticationForm

INPUT_CLASS = (
    "w-full rounded-lg border border-slate-300 px-4 py-2.5 text-slate-800 "
    "placeholder-slate-400 focus:border-brand focus:ring-2 focus:ring-brand/30 "
    "focus:outline-none transition"
)


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"class": INPUT_CLASS, "placeholder": "Usuario", "autofocus": True}
        )
        self.fields["password"].widget.attrs.update(
            {"class": INPUT_CLASS, "placeholder": "Contraseña"}
        )
