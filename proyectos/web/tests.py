"""Tests del reparto de revisión (regla B): el director revisa lo que ejecuta un
coordinador; el coordinador del proyecto revisa lo que ejecuta un formulador."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from contenido.models import Actividades, Proyectos
from web import selectors, services
from web.forms import ActividadForm

Estado = Actividades.EstadoActividad


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
