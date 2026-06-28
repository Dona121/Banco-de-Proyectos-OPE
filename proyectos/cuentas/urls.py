from django.urls import path

from . import views

app_name = "cuentas"

urlpatterns = [
    path("login/", views.AppLoginView.as_view(), name="login"),
    path("logout/", views.AppLogoutView.as_view(), name="logout"),
    path("perfil/", views.PerfilView.as_view(), name="perfil"),
]
