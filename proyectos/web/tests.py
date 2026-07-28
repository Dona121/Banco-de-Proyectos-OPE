"""Tests del reparto de revisión (regla B): el director revisa lo que ejecuta un
coordinador; el coordinador del proyecto revisa lo que ejecuta un formulador."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from contenido.models import Actividades, Proyectos, Revisiones
from web import selectors, services
from web.forms import ActividadForm

Estado = Actividades.EstadoActividad

# Storage en memoria para no subir archivos al S3 real durante los tests.
_STORAGES_TEST = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}


class ReglaBBaseTest(TestCase):
    def setUp(self):
        self.director = self._user("dir", "Director")
        self.coord = self._user("coord", "Coordinador")       # coordinador del proyecto
        self.coord2 = self._user("coord2", "Coordinador")     # coordinador ejecutor
        self.form = self._user("form", "Formulador")
        self.proyecto = Proyectos.objects.create(
            nombre="P1", creador_por=self.director, asignado_a=self.coord
        )

    def _user(self, username, grupo):
        u = User.objects.create_user(username, password="x")
        u.groups.add(Group.objects.get(name=grupo))
        return u

    def _actividad(self, asignado_a, estado=Estado.PENDIENTE):
        ahora = timezone.now()
        return Actividades.objects.create(
            proyecto=self.proyecto, nombre=f"act-{asignado_a.username}",
            fecha_programada=ahora, fecha_vencimiento=ahora + timedelta(days=7),
            estado=estado, asignado_por=self.director, asignado_a=asignado_a,
        )

    def _entrega(self, asignado_a):
        """Actividad asignada a `asignado_a` con una entrega suya (queda EN_REVISION)."""
        act = self._actividad(asignado_a)
        entrega = services.crear_entrega(act, asignado_a, "v1")
        return act, entrega


class ResponsableRevisionTest(ReglaBBaseTest):
    def test_ejecutor_formulador_revisa_coordinador_del_proyecto(self):
        act = self._actividad(self.form)
        self.assertEqual(selectors.responsable_revision(act), self.coord)

    def test_ejecutor_coordinador_revisa_director(self):
        act = self._actividad(self.coord2)
        self.assertEqual(selectors.responsable_revision(act), self.director)


class PuedeRevisarTest(ReglaBBaseTest):
    def test_trabajo_de_formulador(self):
        _, entrega = self._entrega(self.form)
        self.assertTrue(selectors.puede_revisar(self.coord, entrega))
        self.assertFalse(selectors.puede_revisar(self.director, entrega))

    def test_trabajo_de_coordinador(self):
        _, entrega = self._entrega(self.coord2)
        self.assertTrue(selectors.puede_revisar(self.director, entrega))
        self.assertFalse(selectors.puede_revisar(self.coord, entrega))


class VisibilidadEjecutorTest(ReglaBBaseTest):
    def test_coordinador_ve_actividad_que_le_asignaron(self):
        act = self._actividad(self.coord2)
        vis = selectors.actividades_visibles(self.coord2)
        self.assertIn(act, vis)

    def test_entregas_por_revisar_separadas(self):
        _, e_form = self._entrega(self.form)
        _, e_coord = self._entrega(self.coord2)
        del_director = set(selectors.entregas_por_revisar(self.director))
        del_coord = set(selectors.entregas_por_revisar(self.coord))
        self.assertIn(e_coord, del_director)
        self.assertNotIn(e_form, del_director)
        self.assertIn(e_form, del_coord)
        self.assertNotIn(e_coord, del_coord)


class NotificacionesTest(ReglaBBaseTest):
    def _textos(self, user):
        return [n["texto"] for n in services.notificaciones_para(user)]

    def test_director_recibe_revision_de_trabajo_de_coordinador(self):
        act, _ = self._entrega(self.coord2)
        self.assertTrue(any("por revisar" in t for t in self._textos(self.director)))
        # El coordinador del proyecto NO recibe esa revisión.
        self.assertFalse(any("por revisar" in t for t in self._textos(self.coord)))

    def test_coordinador_recibe_revision_de_trabajo_de_formulador(self):
        self._entrega(self.form)
        self.assertTrue(any("por revisar" in t for t in self._textos(self.coord)))
        self.assertFalse(any("por revisar" in t for t in self._textos(self.director)))

    def test_coordinador_ejecutor_recibe_su_asignacion(self):
        self._actividad(self.coord2)  # PENDIENTE, asignada al coordinador ejecutor
        self.assertTrue(any("realízala y entrégala" in t for t in self._textos(self.coord2)))


class ConsultaRolTest(ReglaBBaseTest):
    """El rol transversal Consulta ve todo el sistema pero no puede actuar."""

    def setUp(self):
        super().setUp()
        self.consulta = self._user("consulta", "Consulta")

    def test_ve_proyectos_y_actividades_ajenos(self):
        act = self._actividad(self.form)
        self.assertIn(self.proyecto, selectors.proyectos_visibles(self.consulta))
        self.assertIn(act, selectors.actividades_visibles(self.consulta))

    def test_no_puede_revisar_ni_entregar(self):
        act, entrega = self._entrega(self.form)
        self.assertFalse(selectors.puede_revisar(self.consulta, entrega))
        self.assertFalse(selectors.puede_crear_entrega(self.consulta, act))

    def test_lectura_permitida_en_vistas(self):
        self.client.force_login(self.consulta)
        self.assertEqual(self.client.get("/").status_code, 200)  # dashboard consulta
        self.assertEqual(self.client.get("/proyectos/").status_code, 200)
        self.assertEqual(
            self.client.get(f"/proyectos/{self.proyecto.pk}/").status_code, 200
        )

    def test_accion_denegada_en_vistas(self):
        self.client.force_login(self.consulta)
        self.assertEqual(self.client.get("/proyectos/nuevo/").status_code, 403)
        self.assertEqual(
            self.client.get(
                f"/proyectos/{self.proyecto.pk}/actividades/nueva/"
            ).status_code,
            403,
        )


class ActividadFormTest(ReglaBBaseTest):
    def test_director_ve_coordinadores_y_formuladores(self):
        qs = ActividadForm(user=self.director).fields["asignado_a"].queryset
        ids = set(qs.values_list("id", flat=True))
        self.assertTrue({self.coord.id, self.coord2.id, self.form.id} <= ids)

    def test_coordinador_solo_ve_formuladores(self):
        qs = ActividadForm(user=self.coord).fields["asignado_a"].queryset
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.form.id, ids)
        self.assertNotIn(self.coord.id, ids)
        self.assertNotIn(self.coord2.id, ids)


class DashboardRenderTest(ReglaBBaseTest):
    """Cada rol carga su dashboard (200 + plantilla correcta). Ejercita
    ``DashboardView`` y todas las funciones de ``metrics``."""

    def _dashboard(self, user, template):
        self.client.force_login(user)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, template)

    def test_dashboard_director(self):
        self._actividad(self.form)
        self._dashboard(self.director, "web/dashboard/director.html")

    def test_dashboard_coordinador(self):
        self._actividad(self.form)
        self._dashboard(self.coord, "web/dashboard/coordinador.html")

    def test_dashboard_formulador(self):
        self._actividad(self.form)
        self._dashboard(self.form, "web/dashboard/formulador.html")

    def test_dashboard_consulta(self):
        consulta = self._user("cons", "Consulta")
        self._dashboard(consulta, "web/dashboard/consulta.html")

    def test_dashboard_generico_sin_rol(self):
        sin_rol = User.objects.create_user("sinrol", password="x")
        self._dashboard(sin_rol, "web/dashboard/generico.html")


class ProyectosVisiblesScopeTest(ReglaBBaseTest):
    """Alcance de ``proyectos_visibles`` por rol: cada quien ve solo lo suyo."""

    def test_director_ve_solo_los_que_creo(self):
        otro_dir = self._user("dir2", "Director")
        ajeno = Proyectos.objects.create(
            nombre="Ajeno", creador_por=otro_dir, asignado_a=self.coord
        )
        vis = selectors.proyectos_visibles(self.director)
        self.assertIn(self.proyecto, vis)
        self.assertNotIn(ajeno, vis)

    def test_coordinador_ve_los_asignados(self):
        otro_coord = self._user("coordX", "Coordinador")
        ajeno = Proyectos.objects.create(
            nombre="Ajeno", creador_por=self.director, asignado_a=otro_coord
        )
        vis = selectors.proyectos_visibles(self.coord)
        self.assertIn(self.proyecto, vis)   # asignado_a = coord
        self.assertNotIn(ajeno, vis)

    def test_formulador_ve_donde_tiene_actividad(self):
        self.assertNotIn(self.proyecto, selectors.proyectos_visibles(self.form))
        self._actividad(self.form)
        self.assertIn(self.proyecto, selectors.proyectos_visibles(self.form))


@override_settings(STORAGES=_STORAGES_TEST)
class FlujoProyectosVistasTest(ReglaBBaseTest):
    """Recorre por HTTP las vistas de acción del flujo de proyectos:
    crear proyecto → crear actividad → entregar → adjuntar documento → revisar."""

    def test_director_crea_proyecto(self):
        self.client.force_login(self.director)
        resp = self.client.post(
            "/proyectos/nuevo/", {"nombre": "Proyecto X", "asignado_a": self.coord.id}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Proyectos.objects.filter(nombre="Proyecto X", creador_por=self.director).exists()
        )

    def test_formulador_no_crea_proyecto(self):
        self.client.force_login(self.form)
        self.assertEqual(self.client.get("/proyectos/nuevo/").status_code, 403)

    def test_flujo_actividad_entrega_documento_revision(self):
        # 1. Director crea y asigna una actividad al formulador.
        self.client.force_login(self.director)
        ahora = timezone.now().replace(second=0, microsecond=0)
        resp = self.client.post(
            f"/proyectos/{self.proyecto.pk}/actividades/nueva/",
            {
                "nombre": "Actividad flujo",
                "fecha_programada": ahora.strftime("%Y-%m-%dT%H:%M"),
                "fecha_vencimiento": (ahora + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
                "asignado_a": self.form.id,
            },
        )
        self.assertEqual(resp.status_code, 302)
        act = Actividades.objects.get(nombre="Actividad flujo")
        self.assertEqual(act.asignado_por, self.director)
        self.assertEqual(act.estado, Estado.PENDIENTE)

        # 2. El formulador entrega → la actividad queda En revisión.
        self.client.force_login(self.form)
        resp = self.client.post(
            f"/actividades/{act.pk}/entregas/nueva/", {"comentario": "v1"}
        )
        self.assertEqual(resp.status_code, 302)
        act.refresh_from_db()
        self.assertEqual(act.estado, Estado.EN_REVISION)
        entrega = act.actividadentrega_set.get()

        # 3. El formulador adjunta un documento (usa InMemoryStorage).
        resp = self.client.post(
            f"/entregas/{entrega.pk}/documentos/nuevo/",
            {
                "nombre": "Soporte",
                "archivo": SimpleUploadedFile("s.pdf", b"x", content_type="application/pdf"),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(entrega.documentos_set.count(), 1)

        # 4. El coordinador del proyecto revisa y aprueba → actividad Aprobada.
        self.client.force_login(self.coord)
        resp = self.client.post(
            f"/entregas/{entrega.pk}/revisar/",
            {"resultado": Revisiones.ResultadoRevision.APROBADA, "comentario": "ok"},
        )
        self.assertEqual(resp.status_code, 302)
        act.refresh_from_db()
        self.assertEqual(act.estado, Estado.APROBADA)

    def test_formulador_no_puede_revisar(self):
        _, entrega = self._entrega(self.form)
        self.client.force_login(self.form)
        resp = self.client.post(
            f"/entregas/{entrega.pk}/revisar/",
            {"resultado": Revisiones.ResultadoRevision.APROBADA, "comentario": "x"},
        )
        self.assertEqual(resp.status_code, 403)


class ReportesTest(ReglaBBaseTest):
    """Página de reportes y descargas (Excel / PDF) responden correctamente."""

    def test_index_render(self):
        self.client.force_login(self.director)
        self.assertEqual(self.client.get("/reportes/").status_code, 200)

    def test_excel_proyectos_formulados(self):
        self._actividad(self.form)
        self.client.force_login(self.director)
        resp = self.client.get("/reportes/proyectos-formulados.xlsx")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])

    def test_pdf_avance_por_proyecto(self):
        self._actividad(self.form)
        self.client.force_login(self.director)
        resp = self.client.get("/reportes/avance-por-proyecto.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")


class ActividadEditTest(ReglaBBaseTest):
    """Editar nombre/fechas de una actividad: solo el director o el coordinador
    que la creó (identificado por ``asignado_por``)."""

    def _act_creada_por(self, creador, asignado_a=None):
        ahora = timezone.now()
        return Actividades.objects.create(
            proyecto=self.proyecto, nombre="orig",
            fecha_programada=ahora, fecha_vencimiento=ahora + timedelta(days=7),
            estado=Estado.PENDIENTE, asignado_por=creador,
            asignado_a=asignado_a or self.form,
        )

    def test_creador_puede_editar(self):
        act_dir = self._act_creada_por(self.director)
        act_coord = self._act_creada_por(self.coord)
        self.assertTrue(selectors.puede_editar_actividad(self.director, act_dir))
        self.assertTrue(selectors.puede_editar_actividad(self.coord, act_coord))

    def test_actividad_aprobada_no_se_puede_editar(self):
        # Ni siquiera su creador puede editar una actividad ya aprobada.
        act = self._act_creada_por(self.director)
        act.estado = Estado.APROBADA
        act.save(update_fields=["estado"])
        self.assertFalse(selectors.puede_editar_actividad(self.director, act))
        self.client.force_login(self.director)
        self.assertEqual(self.client.get(f"/actividades/{act.pk}/editar/").status_code, 403)

    def test_no_creador_no_puede_editar(self):
        act_dir = self._act_creada_por(self.director)
        # El coordinador del proyecto, si no la creó, no puede editarla.
        self.assertFalse(selectors.puede_editar_actividad(self.coord, act_dir))
        # Un formulador nunca puede editar.
        self.assertFalse(selectors.puede_editar_actividad(self.form, act_dir))

    def test_post_actualiza_nombre_y_fechas(self):
        act = self._act_creada_por(self.director)
        self.client.force_login(self.director)
        ahora = timezone.now().replace(second=0, microsecond=0)
        resp = self.client.post(
            f"/actividades/{act.pk}/editar/",
            {
                "nombre": "nuevo nombre",
                "fecha_programada": ahora.strftime("%Y-%m-%dT%H:%M"),
                "fecha_vencimiento": (ahora + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(resp.status_code, 302)
        act.refresh_from_db()
        self.assertEqual(act.nombre, "nuevo nombre")

    def test_no_creador_recibe_403(self):
        # La actividad la creó el director; es visible para el coordinador del
        # proyecto, pero no la creó él → 403 (no 404).
        act = self._act_creada_por(self.director)
        self.client.force_login(self.coord)
        self.assertEqual(self.client.get(f"/actividades/{act.pk}/editar/").status_code, 403)

    def test_formulador_recibe_403(self):
        act = self._act_creada_por(self.director)
        self.client.force_login(self.form)
        self.assertEqual(self.client.get(f"/actividades/{act.pk}/editar/").status_code, 403)

    def test_fecha_invalida_no_guarda(self):
        act = self._act_creada_por(self.director)
        self.client.force_login(self.director)
        ahora = timezone.now().replace(second=0, microsecond=0)
        resp = self.client.post(
            f"/actividades/{act.pk}/editar/",
            {
                "nombre": "no debe guardar",
                "fecha_programada": (ahora + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
                "fecha_vencimiento": ahora.strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(resp.status_code, 200)  # re-render con error de validación
        act.refresh_from_db()
        self.assertEqual(act.nombre, "orig")
