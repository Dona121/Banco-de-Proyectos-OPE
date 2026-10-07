"""Tests del reparto de revisión (regla B): el director revisa lo que ejecuta un
coordinador; el coordinador del proyecto revisa lo que ejecuta un formulador."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contenido.models import Actividades, Documentos, Proyectos, Revisiones
from web import selectors, services
from web.forms import ActividadForm

Estado = Actividades.EstadoActividad

# El storage en memoria y la base SQLite los fija `proyectos.test_settings`:
#     uv run python manage.py test --settings=proyectos.test_settings


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


class AislamientoDeDominiosTest(TestCase):
    """Un rol de cuentas de cobro no entra a las vistas del dominio Proyectos.

    Antes recibía 200 en todas ellas: no veía datos (los selectores le devuelven
    vacío) pero contradecía la regla de aislamiento. El panel de inicio es la
    excepción deliberada (es el destino del login de todos los roles) y en vez de
    cerrarlo se redirige a cada uno a su módulo.
    """

    RUTAS = [
        "/proyectos/",
        "/actividades/",
        "/reportes/",
        "/reportes/proyectos-formulados.xlsx",
        "/reportes/avance-por-proyecto.pdf",
    ]

    def _user(self, username, grupo):
        u = User.objects.create_user(username, password="x")
        u.groups.add(Group.objects.get(name=grupo))
        return u

    def test_contratista_no_entra_al_dominio_proyectos(self):
        self.client.force_login(self._user("contra", "Contratista"))
        for ruta in self.RUTAS:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 403)

    def test_contratista_en_la_raiz_va_a_su_bandeja(self):
        self.client.force_login(self._user("contra", "Contratista"))
        resp = self.client.get("/")
        self.assertRedirects(resp, reverse("cuentas_cobro:bandeja"))

    def test_consulta_si_entra_de_solo_lectura(self):
        """El rol transversal es la excepción documentada: ve ambos dominios."""
        self.client.force_login(self._user("consulta", "Consulta"))
        for ruta in self.RUTAS:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_los_roles_de_proyectos_siguen_entrando(self):
        for grupo in ("Director", "Coordinador", "Formulador"):
            user = self._user(f"u{grupo}", grupo)
            self.client.force_login(user)
            for ruta in self.RUTAS + ["/"]:
                with self.subTest(grupo=grupo, ruta=ruta):
                    self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_usuario_sin_ningun_rol_ve_el_aviso(self):
        self.client.force_login(User.objects.create_user("nadie", password="x"))
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("no tiene un rol asignado", resp.content.decode())

    def test_superusuario_entra_a_todo(self):
        self.client.force_login(
            User.objects.create_superuser("admin", "a@b.co", "x")
        )
        for ruta in self.RUTAS + ["/"]:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 200)


class ReportesFiltrosInvalidosTest(ReglaBBaseTest):
    """Un filtro mal formado se descartaba en silencio y salía el reporte COMPLETO."""

    def setUp(self):
        super().setUp()
        self._actividad(self.form, estado=Estado.APROBADA)

    def test_excel_con_fecha_invalida_avisa_y_no_genera(self):
        self.client.force_login(self.director)
        resp = self.client.get(
            "/reportes/proyectos-formulados.xlsx?desde=no-es-fecha", follow=True
        )
        self.assertRedirects(resp, "/reportes/")
        avisos = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("filtros inválidos" in m for m in avisos), avisos)

    def test_pdf_con_proyecto_inexistente_avisa_y_no_genera(self):
        self.client.force_login(self.director)
        resp = self.client.get(
            "/reportes/avance-por-proyecto.pdf?proyecto=999999", follow=True
        )
        self.assertRedirects(resp, "/reportes/")

    def test_sin_filtros_si_genera_el_reporte_completo(self):
        self.client.force_login(self.director)
        resp = self.client.get("/reportes/proyectos-formulados.xlsx")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])

    def test_filtros_validos_si_generan(self):
        self.client.force_login(self.director)
        resp = self.client.get(
            f"/reportes/avance-por-proyecto.pdf?proyecto={self.proyecto.pk}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")


class FiltrosDeProyectosTest(TestCase):
    """Los desplegables ofrecen solo a quien aparece en los proyectos visibles."""

    def setUp(self):
        def u(username, grupo, nombre):
            usuario = User.objects.create_user(username, password="x", first_name=nombre)
            usuario.groups.add(Group.objects.get(name=grupo))
            return usuario

        self.dir_a = u("dirA", "Director", "Ana")
        self.dir_b = u("dirB", "Director", "Beto")
        self.coord_a = u("coordA", "Coordinador", "Carla")
        self.coord_b = u("coordB", "Coordinador", "Diego")
        self.form_a = u("formA", "Formulador", "Elena")
        self.form_b = u("formB", "Formulador", "Fabio")

        ahora = timezone.now()

        def proyecto(nombre, director, coordinador, ejecutor):
            p = Proyectos.objects.create(
                nombre=nombre, creador_por=director, asignado_a=coordinador
            )
            Actividades.objects.create(
                proyecto=p, nombre=f"act {nombre}", fecha_programada=ahora,
                fecha_vencimiento=ahora + timedelta(days=5),
                estado=Estado.PENDIENTE, asignado_por=director, asignado_a=ejecutor,
            )
            return p

        # La coordinadora Carla coordina dos proyectos, de dos directores distintos.
        self.p1 = proyecto("Uno", self.dir_a, self.coord_a, self.form_a)
        self.p2 = proyecto("Dos", self.dir_b, self.coord_a, self.form_b)
        # Y este no es suyo: no debe asomar en sus filtros.
        self.p3 = proyecto("Tres", self.dir_a, self.coord_b, self.form_b)

    def _opciones(self, user):
        op = selectors.opciones_de_filtro(user)
        return {k: {u.username for u in v} for k, v in op.items()}

    def test_coordinador_ve_sus_dos_directores_y_sus_formuladores(self):
        op = self._opciones(self.coord_a)
        self.assertEqual(op["directores"], {"dirA", "dirB"})
        self.assertEqual(op["formuladores"], {"formA", "formB"})
        # Un proyecto tiene un solo coordinador: se ve a sí misma y a nadie más.
        self.assertEqual(op["coordinadores"], {"coordA"})

    def test_director_ve_los_coordinadores_de_sus_proyectos(self):
        op = self._opciones(self.dir_a)
        self.assertEqual(op["directores"], {"dirA"})
        self.assertEqual(op["coordinadores"], {"coordA", "coordB"})

    def test_formulador_ve_solo_lo_de_sus_proyectos(self):
        op = self._opciones(self.form_a)
        self.assertEqual(op["directores"], {"dirA"})
        self.assertEqual(op["coordinadores"], {"coordA"})
        self.assertEqual(op["formuladores"], {"formA"})

    def test_consulta_ve_a_todos(self):
        consulta = User.objects.create_user("consulta", password="x")
        consulta.groups.add(Group.objects.get(name="Consulta"))
        op = self._opciones(consulta)
        self.assertEqual(op["directores"], {"dirA", "dirB"})
        self.assertEqual(op["coordinadores"], {"coordA", "coordB"})
        self.assertEqual(op["formuladores"], {"formA", "formB"})

    def test_filtrar_por_director_reduce_la_lista(self):
        self.client.force_login(self.coord_a)
        resp = self.client.get(f"/proyectos/?director={self.dir_b.pk}")
        self.assertEqual([p.nombre for p in resp.context["proyectos"]], ["Dos"])

    def test_filtrar_por_formulador_no_duplica_proyectos(self):
        ahora = timezone.now()
        # Dos actividades del mismo formulador en el mismo proyecto: sin distinct
        # el proyecto saldría repetido.
        Actividades.objects.create(
            proyecto=self.p1, nombre="otra", fecha_programada=ahora,
            fecha_vencimiento=ahora + timedelta(days=5), estado=Estado.PENDIENTE,
            asignado_por=self.dir_a, asignado_a=self.form_a,
        )
        self.client.force_login(self.coord_a)
        resp = self.client.get(f"/proyectos/?formulador={self.form_a.pk}")
        self.assertEqual([p.nombre for p in resp.context["proyectos"]], ["Uno"])

    def test_un_id_no_numerico_no_revienta(self):
        self.client.force_login(self.coord_a)
        resp = self.client.get("/proyectos/?director=abc&coordinador=x&formulador=-")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["filtros"]["director"], "")
        self.assertEqual(len(resp.context["proyectos"]), 2)

    def test_se_pintan_los_tres_desplegables(self):
        """Basta con que haya alguien a quien filtrar.

        Antes se exigían dos o más opciones, y con pocos usuarios no aparecía
        ninguno de los tres: el filtro de coordinador, por ejemplo, solo ofrece a
        la propia coordinadora, porque un proyecto tiene un único coordinador.
        """
        self.client.force_login(self.coord_a)
        cuerpo = self.client.get("/proyectos/").content.decode()
        for campo in ("director", "coordinador", "formulador"):
            with self.subTest(campo=campo):
                self.assertIn(f'name="{campo}"', cuerpo)

    def test_un_desplegable_sin_nadie_no_se_pinta(self):
        """Un formulador sin actividades de nadie más no ve ese filtro vacío."""
        huerfano = User.objects.create_user("sin_nada", password="x")
        huerfano.groups.add(Group.objects.get(name="Formulador"))
        self.client.force_login(huerfano)
        cuerpo = self.client.get("/proyectos/").content.decode()
        self.assertNotIn('name="director"', cuerpo)
        self.assertNotIn('name="formulador"', cuerpo)

    def test_los_filtros_sobreviven_a_la_paginacion(self):
        """La paginación concatenaba a mano unos pocos parámetros y perdía el resto."""
        ahora = timezone.now()
        for i in range(14):
            Proyectos.objects.create(
                nombre=f"Extra {i}", creador_por=self.dir_b, asignado_a=self.coord_a
            )
        self.client.force_login(self.coord_a)
        resp = self.client.get(f"/proyectos/?director={self.dir_b.pk}&q=Extra")
        self.assertTrue(resp.context["is_paginated"])
        cuerpo = resp.content.decode()
        self.assertIn(f"director={self.dir_b.pk}", cuerpo)
        self.assertIn("q=Extra", cuerpo)
        self.assertIn("page=2", cuerpo)


class SubidaDirectaProyectosTest(ReglaBBaseTest):
    """Los documentos de una entrega también suben directo al bucket."""

    def setUp(self):
        super().setUp()
        self.actividad = self._actividad(self.form)
        self.entrega = services.crear_entrega(self.actividad, self.form, "v1")

    def _firmar(self, user, **datos):
        self.client.force_login(user)
        return self.client.post(f"/entregas/{self.entrega.pk}/subir/firmar/", datos)

    def test_el_ejecutor_pasa_las_validaciones(self):
        resp = self._firmar(
            self.form, nombre_documento="Soporte", nombre="soporte.pdf"
        )
        # Sin bucket no se puede firmar, pero el permiso y el nombre ya pasaron.
        self.assertEqual(resp.status_code, 400)
        self.assertIn("almacenamiento", resp.json()["error"])

    def test_quien_no_hizo_la_entrega_no_sube(self):
        resp = self._firmar(self.coord, nombre_documento="X", nombre="a.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_exige_nombre_del_documento(self):
        resp = self._firmar(self.form, nombre_documento="  ", nombre="a.pdf")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nombre del documento", resp.json()["error"])

    def test_rechaza_una_extension_no_admitida(self):
        resp = self._firmar(self.form, nombre_documento="X", nombre="virus.exe")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no es un tipo de archivo admitido", resp.json()["error"])

    def test_no_confirma_con_token_invalido(self):
        self.client.force_login(self.form)
        resp = self.client.post(
            f"/entregas/{self.entrega.pk}/subir/confirmar/", {"token": "falso"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.entrega.documentos_set.count(), 0)

    def test_la_actividad_aprobada_cierra_la_subida(self):
        self.actividad.estado = Estado.APROBADA
        self.actividad.save(update_fields=["estado"])
        resp = self._firmar(self.form, nombre_documento="X", nombre="a.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_el_detalle_pinta_el_componente(self):
        self.client.force_login(self.form)
        cuerpo = self.client.get(f"/entregas/{self.entrega.pk}/").content.decode()
        self.assertIn(f"/entregas/{self.entrega.pk}/subir/firmar/", cuerpo)
        self.assertIn('name="nombre_documento"', cuerpo)
        self.assertIn("progress", cuerpo)
        # Y el botón de cancelar, para cuando el archivo elegido no era el bueno.
        self.assertIn("cancelar", cuerpo)
        # El mismo paso de confirmación que en cuentas de cobro: la pausa antes
        # de enviar es igual en todo el sistema, no una excepción de un módulo.
        self.assertIn("Revisa antes de enviar", cuerpo)
        self.assertIn("Confirmar envío", cuerpo)
        self.assertIn("Volver a elegir", cuerpo)


class DescargaDeDocumentosTest(ReglaBBaseTest):
    """La descarga comprueba el acceso y baja el archivo con su nombre."""

    def setUp(self):
        super().setUp()
        self.actividad = self._actividad(self.form)
        self.entrega = services.crear_entrega(self.actividad, self.form, "v1")
        self.documento = Documentos.objects.create(
            actividad_entrega=self.entrega, nombre="Informe de avance",
            archivo=SimpleUploadedFile("x.pdf", b"x", content_type="application/pdf"),
        )
        self.url = f"/documentos/{self.documento.pk}/descargar/"

    def test_quien_ve_la_entrega_llega_al_almacenamiento(self):
        self.client.force_login(self.coord)  # coordinador del proyecto: la ve
        resp = self.client.get(self.url, follow=True)
        avisos = " ".join(m.message for m in resp.context["messages"])
        self.assertIn("almacenamiento", avisos)

    def test_un_formulador_ajeno_no_descarga(self):
        ajeno = self._user("otro_form", "Formulador")
        self.client.force_login(ajeno)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_un_rol_de_cuentas_de_cobro_no_entra(self):
        contratista = self._user("contra", "Contratista")
        self.client.force_login(contratista)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_el_detalle_ofrece_descargar(self):
        self.client.force_login(self.form)
        cuerpo = self.client.get(f"/entregas/{self.entrega.pk}/").content.decode()
        self.assertIn(self.url, cuerpo)
        self.assertIn("Descargar", cuerpo)


class EliminarDocumentoProyectosTest(ReglaBBaseTest):
    """Quitar un adjunto de la entrega: quien la hizo, si no está aprobada."""

    def setUp(self):
        super().setUp()
        self.actividad = self._actividad(self.form)
        self.entrega = services.crear_entrega(self.actividad, self.form, "v1")
        self.documento = Documentos.objects.create(
            actividad_entrega=self.entrega, nombre="Soporte",
            archivo=SimpleUploadedFile("x.pdf", b"x", content_type="application/pdf"),
        )
        self.url = f"/documentos/{self.documento.pk}/eliminar/"

    def test_el_ejecutor_lo_quita(self):
        self.client.force_login(self.form)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.entrega.documentos_set.count(), 0)

    def test_el_coordinador_que_revisa_no_lo_quita(self):
        self.client.force_login(self.coord)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertEqual(self.entrega.documentos_set.count(), 1)

    def test_con_la_actividad_aprobada_no_se_puede(self):
        self.actividad.estado = Estado.APROBADA
        self.actividad.save(update_fields=["estado"])
        self.client.force_login(self.form)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertEqual(self.entrega.documentos_set.count(), 1)

    def test_no_se_quita_con_get(self):
        self.client.force_login(self.form)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.entrega.documentos_set.count(), 1)


class FlujogramaEnProyectosTest(ReglaBBaseTest):
    """La lista de proyectos ofrece el flujograma, como la de actividades."""

    def test_el_boton_y_el_modal_estan(self):
        self.client.force_login(self.coord)
        cuerpo = self.client.get("/proyectos/").content.decode()
        self.assertIn("Flujograma del proceso", cuerpo)
        self.assertIn('x-data="{ flujograma:false }"', cuerpo)
        # El modal se teletransporta al body (si no, queda atrapado en <main>).
        self.assertIn('x-teleport="body"', cuerpo)

    def test_lo_ve_cualquier_rol_del_dominio(self):
        for user in (self.director, self.coord, self.form):
            self.client.force_login(user)
            cuerpo = self.client.get("/proyectos/").content.decode()
            self.assertIn("Flujograma del proceso", cuerpo)


class NombreSeguroTest(TestCase):
    """Un nombre con tildes hacía fallar la subida con un 500."""

    def test_transliteracion(self):
        from cuentas.validadores import LARGO_MAXIMO_NOMBRE, nombre_seguro

        casos = {
            "Documentación.pdf": "Documentacion.pdf",
            "ñandú.docx": "nandu.docx",
            "Año 2026: informe.PDF": "Ano_2026_informe.pdf",
            "planilla seguridad social.pdf": "planilla_seguridad_social.pdf",
            "sin_extension": "sin_extension",
            # Alfabeto sin equivalente latino: no queda nada que transliterar.
            "документы.pdf": "archivo.pdf",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(nombre_seguro(entrada), esperado)

        largo = nombre_seguro("á" * 300 + ".pdf")
        self.assertTrue(largo.endswith(".pdf"))
        self.assertLessEqual(len(largo) - len(".pdf"), LARGO_MAXIMO_NOMBRE)

    def test_no_arrastra_rutas(self):
        from cuentas.validadores import nombre_seguro

        self.assertEqual(nombre_seguro("../../etc/passwd.pdf"), "passwd.pdf")


class ValidacionDeArchivosTest(ReglaBBaseTest):
    """Ningún formulario con FileField validaba extensión ni tamaño."""

    def test_un_nombre_con_tildes_se_guarda_normalizado(self):
        act = self._actividad(self.form)
        entrega = services.crear_entrega(act, self.form, "v1")
        self.client.force_login(self.form)
        resp = self.client.post(
            f"/entregas/{entrega.pk}/documentos/nuevo/",
            {
                "nombre": "Soporte",
                "archivo": SimpleUploadedFile(
                    "Documentación año 2026.pdf", b"x", content_type="application/pdf"
                ),
            },
        )
        self.assertEqual(resp.status_code, 302)
        doc = entrega.documentos_set.get()
        self.assertEqual(doc.archivo.name, "Documentacion_ano_2026.pdf")
        self.assertTrue(doc.archivo.name.isascii())

    def _entrega_del_formulador(self):
        act = self._actividad(self.form)
        return services.crear_entrega(act, self.form, "v1")

    def test_rechaza_una_extension_no_admitida(self):
        entrega = self._entrega_del_formulador()
        self.client.force_login(self.form)
        resp = self.client.post(
            f"/entregas/{entrega.pk}/documentos/nuevo/",
            {
                "nombre": "Soporte",
                "archivo": SimpleUploadedFile(
                    "virus.exe", b"MZ", content_type="application/octet-stream"
                ),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(entrega.documentos_set.count(), 0)

    def test_rechaza_un_archivo_demasiado_grande(self):
        from cuentas.validadores import TAMANO_MAXIMO

        entrega = self._entrega_del_formulador()
        self.client.force_login(self.form)
        grande = SimpleUploadedFile(
            "enorme.pdf", b"x" * (TAMANO_MAXIMO + 1), content_type="application/pdf"
        )
        self.client.post(
            f"/entregas/{entrega.pk}/documentos/nuevo/",
            {"nombre": "Soporte", "archivo": grande},
        )
        self.assertEqual(entrega.documentos_set.count(), 0)

    def test_acepta_un_pdf_normal(self):
        entrega = self._entrega_del_formulador()
        self.client.force_login(self.form)
        self.client.post(
            f"/entregas/{entrega.pk}/documentos/nuevo/",
            {
                "nombre": "Soporte",
                "archivo": SimpleUploadedFile(
                    "soporte.pdf", b"contenido", content_type="application/pdf"
                ),
            },
        )
        self.assertEqual(entrega.documentos_set.count(), 1)


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

    def test_filtro_de_proyecto_no_numerico_no_revienta(self):
        """El id sale de la querystring: si no es un número, filtrar por él hacía
        que el ORM lanzara ValueError y la lista respondiera 500."""
        self.client.force_login(self.coord)
        resp = self.client.get("/actividades/?proyecto=abc")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["proyecto_sel"], "")

    def test_filtro_de_proyecto_valido_si_filtra(self):
        act = self._actividad(self.form)
        otro = Proyectos.objects.create(
            nombre="P2", creador_por=self.director, asignado_a=self.coord
        )
        self.client.force_login(self.coord)
        resp = self.client.get(f"/actividades/?proyecto={self.proyecto.pk}")
        self.assertIn(act, resp.context["actividades"])
        resp = self.client.get(f"/actividades/?proyecto={otro.pk}")
        self.assertNotIn(act, resp.context["actividades"])

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
