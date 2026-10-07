from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),

    # Proyectos
    path("proyectos/", views.ProyectoListView.as_view(), name="proyectos"),
    path("proyectos/nuevo/", views.ProyectoCreateView.as_view(), name="proyecto_nuevo"),
    path("proyectos/<int:pk>/", views.ProyectoDetailView.as_view(), name="proyecto_detalle"),
    path("proyectos/<int:pk>/editar/", views.ProyectoUpdateView.as_view(), name="proyecto_editar"),
    path("proyectos/<int:proyecto_pk>/actividades/nueva/",
         views.ActividadCreateView.as_view(), name="actividad_nueva"),

    # Actividades
    path("actividades/", views.ActividadListView.as_view(), name="actividades"),
    path("actividades/<int:pk>/", views.ActividadDetailView.as_view(), name="actividad_detalle"),
    path("actividades/<int:pk>/editar/", views.ActividadUpdateView.as_view(), name="actividad_editar"),
    path("actividades/<int:actividad_pk>/subactividades/nueva/",
         views.SubactividadCreateView.as_view(), name="subactividad_nueva"),
    path("actividades/<int:actividad_pk>/entregas/nueva/",
         views.EntregaCreateView.as_view(), name="entrega_nueva"),

    # Entregas
    path("entregas/<int:pk>/", views.EntregaDetailView.as_view(), name="entrega_detalle"),
    path("entregas/<int:entrega_pk>/documentos/nuevo/",
         views.DocumentoCreateView.as_view(), name="documento_nuevo"),
    # Subida directa al bucket: el archivo no pasa por el servidor.
    path("entregas/<int:entrega_pk>/subir/firmar/",
         views.SubidaFirmarView.as_view(), name="subida_firmar"),
    path("entregas/<int:entrega_pk>/subir/confirmar/",
         views.SubidaConfirmarView.as_view(), name="subida_confirmar"),
    # Descarga con nombre legible, comprobando antes que la entrega sea visible.
    path("documentos/<int:pk>/descargar/",
         views.DescargaDocumentoView.as_view(), name="documento_descargar"),
    path("documentos/<int:pk>/eliminar/",
         views.DocumentoEliminarView.as_view(), name="documento_eliminar"),
    path("entregas/<int:entrega_pk>/revisar/",
         views.RevisionCreateView.as_view(), name="revision_nueva"),

    # Reportes
    path("reportes/", views.ReportesIndexView.as_view(), name="reportes"),
    path("reportes/proyectos-formulados.xlsx",
         views.reporte_formulados_excel, name="reporte_formulados_excel"),
    path("reportes/avance-por-proyecto.pdf",
         views.reporte_avance_pdf, name="reporte_avance_pdf"),
]
