from django.urls import path

from . import views

app_name = "cuentas_cobro"

urlpatterns = [
    path("", views.BandejaView.as_view(), name="bandeja"),
    path("nueva/", views.CuentaCreateView.as_view(), name="cuenta_nueva"),
    path("<int:pk>/", views.CuentaDetailView.as_view(), name="cuenta_detalle"),

    # Contratista
    path("<int:pk>/documentos/cargar/",
         views.DocumentoCargarView.as_view(), name="documento_cargar"),
    path("<int:pk>/entregar/", views.EntregarView.as_view(), name="entregar"),

    # Radicación (supervisor / rol de radicación)
    path("<int:pk>/radicacion/",
         views.RevisionRadicacionView.as_view(), name="radicacion"),
    path("documentos/<int:doc_pk>/revisar/",
         views.DocumentoRevisarView.as_view(), name="documento_revisar"),

    # Supervisor — asignación
    path("<int:pk>/asignar/", views.AsignarRevisorView.as_view(), name="asignar"),
    path("asignaciones/<int:asignacion_pk>/reasignar/",
         views.ReasignarView.as_view(), name="reasignar"),

    # Revisor
    path("asignaciones/<int:asignacion_pk>/declinar/",
         views.DeclinarView.as_view(), name="declinar"),
    path("asignaciones/<int:asignacion_pk>/revisar/",
         views.RevisionView.as_view(), name="revisar"),

    # Supervisor — decisión final · Contratista — cierre · Trámites finales
    path("<int:pk>/decision/",
         views.DecisionSupervisorView.as_view(), name="decision"),
    path("<int:pk>/cierre/cargar/",
         views.DocumentoCierreView.as_view(), name="cierre_cargar"),
    path("<int:pk>/tramite/<str:tipo>/",
         views.TramiteFinalView.as_view(), name="tramite"),
]
