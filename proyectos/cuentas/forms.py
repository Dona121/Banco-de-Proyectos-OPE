"""Formularios de cuentas (login y cambio de contraseña con estilos de marca)."""
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

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


class CambiarPasswordForm(PasswordChangeForm):
    """Cambio de contraseña del usuario autenticado, con estilos de marca."""

    _PLACEHOLDERS = {
        "old_password": "Contraseña actual",
        "new_password1": "Nueva contraseña",
        "new_password2": "Repite la nueva contraseña",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, field in self.fields.items():
            field.widget.attrs.update(
                {"class": INPUT_CLASS, "placeholder": self._PLACEHOLDERS.get(nombre, "")}
            )
