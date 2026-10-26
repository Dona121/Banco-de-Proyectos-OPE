"""Control de acceso y errores comunes, en los dos dominios a la vez.

Lo que se ataca aquí no es la lógica de negocio (eso está en los dos
`test_invariantes`), sino lo que hace un usuario real sin querer o con ganas:
pulsar dos veces, volver atrás, abrir a mano un enlace que no le toca, llegar
sin sesión, o editar el número de la URL.

La regla que se comprueba en todo el archivo: **nada de eso puede devolver un
5xx**. Una denegación es 403 o 404; un dato mal escrito es un 400 o la misma
página repintada con el motivo. Un 500 significa que la aplicación se rompió y
el usuario no tiene forma de saber qué pasó.
"""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from contenido.models import Actividades, Documentos, Proyectos, Revisiones
from cuentas.models import AdjuntoDeRevision
from cuentas_de_cobro import services as cc
from cuentas_de_cobro.models import (
    AsignacionRevisor,
    CuentaEntrega,
    DocumentosCuentaCobro,
    RequisitoDocumental,
    RevisionCuentaCobro,
    RevisionParaRadicacion,
    TipoDocumentoCargue,
    TramiteFinal,
    Vigencia,
)
from web import services as web_services

# Respuestas admisibles: permiso denegado, no existe, método que no aplica,
# datos inválidos, redirección. Lo que NO es admisible es un 5xx.
ACEPTABLES = (200, 302, 400, 403, 404, 405)

# La pantalla de inicio es el destino del login de TODOS los roles, así que no
# lleva mixin de módulo a propósito: no cuenta como "sección de Proyectos".
COMUNES = {"web:dashboard"}

SIIF = TramiteFinal.Tipo.CARGUE_SIIFWEB
SECOP = TramiteFinal.Tipo.CARGUE_SECOP


def _archivo(nombre="d.pdf"):
    return SimpleUploadedFile(nombre, b"x", content_type="application/pdf")


class EscenarioCompleto(TestCase):
    """Un expediente vivo de cada dominio y un usuario de cada rol."""

    def setUp(self):
        self.usuarios = {}
        for nombre, grupo in (
            ("dir", "Director"), ("coord", "Coordinador"), ("form", "Formulador"),
            ("contratista", "Contratista"), ("supervisor", "Supervisor"),
            ("radicador", "Radicacion"), ("secop", "Secop"),
            ("rev_te", "Revisor"), ("rev_ju", "Revisor"), ("rev_ad", "Revisor"),
            ("consulta", "Consulta"),
        ):
            u = User.objects.create_user(nombre, password="x")
            u.groups.add(Group.objects.get(name=grupo))
            self.usuarios[nombre] = u

        # --- Proyectos: una entrega ya realizada, con su documento y un soporte.
        self.proyecto = Proyectos.objects.create(
            nombre="P", creador_por=self.usuarios["dir"],
            asignado_a=self.usuarios["coord"],
        )
        ahora = timezone.now()
        self.actividad = Actividades.objects.create(
            proyecto=self.proyecto, nombre="act", fecha_programada=ahora,
            fecha_vencimiento=ahora + timedelta(days=3),
            estado=Actividades.EstadoActividad.PENDIENTE,
            asignado_por=self.usuarios["dir"], asignado_a=self.usuarios["form"],
        )
        self.entrega = web_services.crear_entrega(
            self.actividad, self.usuarios["form"], "v1"
        )
        self.documento = Documentos.objects.create(
            actividad_entrega=self.entrega, nombre="d", archivo=_archivo()
        )
        web_services.realizar_entrega(self.entrega, self.usuarios["form"])
        self.adjunto = AdjuntoDeRevision.objects.create(
            content_type=ContentType.objects.get_for_model(self.entrega),
            object_id=self.entrega.pk, usuario=self.usuarios["coord"],
            nombre="soporte", archivo=_archivo("s.pdf"),
        )

        # --- Cuentas de cobro: una cuenta entregada, con sus dos documentos.
        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.t1 = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        self.t2 = TipoDocumentoCargue.objects.create(nombre="Planilla")
        for t in (self.t1, self.t2):
            RequisitoDocumental.objects.create(vigencia=self.vigencia, tipo_documento=t)
        self.cuenta = cc.crear_cuenta(
            self.usuarios["contratista"], self.vigencia, 6, "junio"
        )
        version = cc.ultima_entrega(self.cuenta)
        self.doc_cc = cc.adjuntar_documento(version, self.t1, _archivo())
        cc.adjuntar_documento(version, self.t2, _archivo())
        cc.entregar(self.cuenta, self.usuarios["contratista"])
        self.cuenta.refresh_from_db()

    # -- avances del flujo de cuentas de cobro -------------------------------- #
    def _aprobar_documentos(self):
        for d in cc.ultima_entrega(self.cuenta).documentoscuentacobro_set.all():
            cc.revisar_documento(d, DocumentosCuentaCobro.EstadoDocumento.APROBADO)

    def _radicar(self):
        self._aprobar_documentos()
        cc.registrar_revision_radicacion(
            self.cuenta, self.usuarios["radicador"],
            RevisionParaRadicacion.ResultadoRevision.APROBADA, "ok",
        )
        self.cuenta.refresh_from_db()

    def _asignar(self):
        self._radicar()
        self.asignaciones = {
            rol: cc.asignar_revisor(
                self.cuenta, rol, self.usuarios[quien], self.usuarios["supervisor"]
            )
            for rol, quien in (
                (AsignacionRevisor.Rol.JURIDICO, "rev_ju"),
                (AsignacionRevisor.Rol.ADMINISTRATIVO, "rev_ad"),
                (AsignacionRevisor.Rol.TECNICO, "rev_te"),
            )
        }
        self.cuenta.refresh_from_db()

    def _hasta_el_cierre(self):
        """Deja la cuenta con el cierre cargado, es decir SIIFWEB habilitado."""
        self._asignar()
        for rol in (AsignacionRevisor.Rol.TECNICO, AsignacionRevisor.Rol.JURIDICO,
                    AsignacionRevisor.Rol.ADMINISTRATIVO):
            cc.registrar_revision(
                self.asignaciones[rol],
                RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok",
            )
        self.cuenta.refresh_from_db()
        cc.decidir_supervisor(
            self.cuenta, self.usuarios["supervisor"],
            CuentaEntrega.ResultadoRevision.APROBADA, "ok",
        )
        self.cuenta.refresh_from_db()
        for t in (self.t1, self.t2):
            cc.cargar_documento_cierre(
                self.cuenta, t, _archivo(), self.usuarios["radicador"]
            )
        self.cuenta.refresh_from_db()

    # -- la matriz ------------------------------------------------------------ #
    def rutas(self):
        """Toda URL de acción de los dos dominios, con argumentos reales.

        Un método y no una constante porque los ids salen del escenario. El
        método es el natural de cada vista: así un 405 es una anomalía y no
        ruido de fondo.
        """
        p, a, e = self.proyecto.pk, self.actividad.pk, self.entrega.pk
        d, adj = self.documento.pk, self.adjunto.pk
        c, dcc = self.cuenta.pk, self.doc_cc.pk
        filas = [
            ("GET", "web:dashboard", {}),
            ("GET", "web:proyectos", {}),
            ("POST", "web:proyecto_nuevo", {}),
            ("GET", "web:proyecto_detalle", {"pk": p}),
            ("POST", "web:proyecto_editar", {"pk": p}),
            ("POST", "web:actividad_nueva", {"proyecto_pk": p}),
            ("GET", "web:actividades", {}),
            ("GET", "web:actividad_detalle", {"pk": a}),
            ("POST", "web:actividad_editar", {"pk": a}),
            ("POST", "web:subactividad_nueva", {"actividad_pk": a}),
            ("POST", "web:entrega_nueva", {"actividad_pk": a}),
            ("GET", "web:entrega_detalle", {"pk": e}),
            ("POST", "web:entrega_realizar", {"pk": e}),
            ("POST", "web:documento_nuevo", {"entrega_pk": e}),
            ("POST", "web:subida_firmar", {"entrega_pk": e}),
            ("POST", "web:subida_confirmar", {"entrega_pk": e}),
            ("GET", "web:documento_descargar", {"pk": d}),
            ("POST", "web:documento_eliminar", {"pk": d}),
            ("POST", "web:adjunto_revision_firmar", {"entrega_pk": e}),
            ("POST", "web:adjunto_revision_confirmar", {"entrega_pk": e}),
            ("GET", "web:adjunto_revision_descargar", {"pk": adj}),
            ("POST", "web:adjunto_revision_eliminar", {"pk": adj}),
            ("POST", "web:revision_nueva", {"entrega_pk": e}),
            ("GET", "web:reportes", {}),
            ("GET", "web:reporte_formulados_excel", {}),
            ("GET", "web:reporte_avance_pdf", {}),
            ("GET", "cuentas_cobro:bandeja", {}),
            ("POST", "cuentas_cobro:cuenta_nueva", {}),
            ("GET", "cuentas_cobro:cuenta_detalle", {"pk": c}),
            ("POST", "cuentas_cobro:documento_cargar", {"pk": c}),
            ("POST", "cuentas_cobro:entregar", {"pk": c}),
            ("POST", "cuentas_cobro:radicacion", {"pk": c}),
            ("POST", "cuentas_cobro:documento_revisar", {"doc_pk": dcc}),
            ("POST", "cuentas_cobro:asignar", {"pk": c}),
            ("POST", "cuentas_cobro:decision", {"pk": c}),
            ("POST", "cuentas_cobro:cierre_cargar", {"pk": c}),
            ("POST", "cuentas_cobro:tramite", {"pk": c, "tipo": SIIF}),
            ("GET", "cuentas_cobro:descargar",
             {"pk": c, "tipo": "documento", "doc_pk": dcc}),
            ("POST", "cuentas_cobro:eliminar_archivo",
             {"pk": c, "tipo": "documento", "doc_pk": dcc}),
        ]
        # Los cuatro destinos de subida comparten vista, pero cada uno tiene su
        # propio permiso: los cuatro entran en la matriz.
        for destino in ("entrega", "cierre", "tramite", "adjunto_revision"):
            filas.append(("POST", "cuentas_cobro:subida_firmar",
                          {"pk": c, "destino": destino}))
            filas.append(("POST", "cuentas_cobro:subida_confirmar",
                          {"pk": c, "destino": destino}))
        # Las tres rutas de asignación entran SIEMPRE, con un id inexistente
        # cuando el escenario todavía no ha asignado revisores: así la prueba de
        # cobertura no deja de ver una ruta solo porque el flujo no llegó ahí.
        asignacion = self.cuenta.asignacionrevisor_set.first()
        for nombre in ("revisar", "declinar", "reasignar"):
            filas.append(("POST", f"cuentas_cobro:{nombre}", {
                "asignacion_pk": asignacion.pk if asignacion else 999999}))
        return filas

    def urls(self):
        return [(m, n, reverse(n, kwargs=kw)) for m, n, kw in self.rutas()]


class SinSesionTest(EscenarioCompleto):
    def test_nada_responde_200_ni_se_rompe(self):
        """Sin sesión, todo tiene que llevar al login. Ni un 200 ni un 5xx."""
        login = reverse("cuentas:login")
        malos = []
        for metodo, nombre, ruta in self.urls():
            r = self.client.generic(metodo, ruta)
            if r.status_code != 302:
                malos.append((nombre, metodo, r.status_code))
            elif login not in r["Location"]:
                malos.append((nombre, metodo, f"302 a {r['Location']}"))
        self.assertEqual(malos, [])


class RolEquivocadoTest(EscenarioCompleto):
    """Aislamiento entre dominios y entre roles, por la puerta de atrás."""

    def _barrer(self, usuario, filtro, esperado=(403, 404)):
        self.client.force_login(usuario)
        malos = []
        for metodo, nombre, ruta in self.urls():
            if nombre in COMUNES or not filtro(nombre, metodo):
                continue
            r = self.client.generic(metodo, ruta)
            if r.status_code not in esperado:
                malos.append((nombre, metodo, r.status_code))
        return malos

    def test_un_contratista_no_toca_nada_de_proyectos(self):
        malos = self._barrer(
            self.usuarios["contratista"], lambda n, m: n.startswith("web:")
        )
        self.assertEqual(malos, [], "un rol de cuentas de cobro entró en Proyectos")

    def test_un_formulador_no_toca_nada_de_cuentas_de_cobro(self):
        malos = self._barrer(
            self.usuarios["form"], lambda n, m: n.startswith("cuentas_cobro:")
        )
        self.assertEqual(malos, [], "un rol de Proyectos entró en cuentas de cobro")

    # `web:entrega_realizar` no lleva mixin de rol propio porque la entrega la
    # realiza cualquier rol de Proyectos sobre SU borrador: quien decide es
    # `puede_documentar`, dentro del servicio. Un rol de solo lectura entra en
    # la vista y sale con un mensaje de error, no con un 403. La prueba de abajo
    # comprueba lo que de verdad importa, que no la realiza.
    SIN_MIXIN_DE_ROL = {"web:entrega_realizar"}

    def test_consulta_no_ejecuta_ninguna_accion(self):
        """Ve los dos dominios, pero no puede hacer nada en ninguno."""
        malos = self._barrer(
            self.usuarios["consulta"],
            lambda n, m: m == "POST" and n not in self.SIN_MIXIN_DE_ROL,
        )
        self.assertEqual(malos, [], "el rol de solo lectura ejecutó una acción")

    def test_consulta_no_realiza_la_entrega_de_otro(self):
        """El borrador es de su autor, y el servicio es quien lo defiende."""
        act = Actividades.objects.create(
            proyecto=self.proyecto, nombre="borrador",
            fecha_programada=timezone.now(),
            fecha_vencimiento=timezone.now() + timedelta(days=2),
            estado=Actividades.EstadoActividad.PENDIENTE,
            asignado_por=self.usuarios["dir"], asignado_a=self.usuarios["form"],
        )
        e = web_services.crear_entrega(act, self.usuarios["form"], "v1")
        Documentos.objects.create(
            actividad_entrega=e, nombre="d", archivo=_archivo()
        )
        self.client.force_login(self.usuarios["consulta"])
        r = self.client.post(reverse("web:entrega_realizar", kwargs={"pk": e.pk}))
        self.assertIn(r.status_code, ACEPTABLES)
        act.refresh_from_db()
        self.assertEqual(
            act.estado, Actividades.EstadoActividad.PENDIENTE,
            "un rol de solo lectura realizó la entrega de otro",
        )

    def test_un_formulador_ajeno_no_revisa_ni_documenta(self):
        """El dominio es el suyo, pero la entrega no."""
        ajeno = User.objects.create_user("ajeno", password="x")
        ajeno.groups.add(Group.objects.get(name="Formulador"))
        self.client.force_login(ajeno)
        acciones = {
            "web:revision_nueva", "web:entrega_realizar", "web:documento_nuevo",
            "web:documento_eliminar", "web:subida_firmar", "web:subida_confirmar",
            "web:adjunto_revision_firmar", "web:adjunto_revision_eliminar",
            "web:entrega_nueva", "web:documento_descargar",
            "web:adjunto_revision_descargar",
        }
        malos = []
        for metodo, nombre, ruta in self.urls():
            if nombre not in acciones:
                continue
            r = self.client.generic(metodo, ruta)
            if r.status_code not in (403, 404):
                malos.append((nombre, r.status_code))
        self.assertEqual(malos, [])

    def test_un_revisor_sin_asignacion_no_revisa_la_cuenta(self):
        suelto = User.objects.create_user("suelto", password="x")
        suelto.groups.add(Group.objects.get(name="Revisor"))
        self._asignar()
        a_te = self.asignaciones[AsignacionRevisor.Rol.TECNICO]
        self.client.force_login(suelto)
        r = self.client.post(
            reverse("cuentas_cobro:revisar", kwargs={"asignacion_pk": a_te.pk}),
            {"resultado": "AP", "comentario": "no es mía"},
        )
        self.assertIn(r.status_code, (403, 404))
        self.assertFalse(
            RevisionCuentaCobro.objects.filter(asignacion=a_te).exists()
        )

    def test_un_revisor_no_firma_el_turno_de_otro_rol(self):
        """Con asignación propia, pero sobre la de un compañero."""
        self._asignar()
        ajena = self.asignaciones[AsignacionRevisor.Rol.JURIDICO]
        self.client.force_login(self.usuarios["rev_te"])
        r = self.client.post(
            reverse("cuentas_cobro:revisar", kwargs={"asignacion_pk": ajena.pk}),
            {"resultado": "AP", "comentario": "firmo por el otro"},
        )
        self.assertIn(r.status_code, (403, 404))
        self.assertFalse(RevisionCuentaCobro.objects.filter(asignacion=ajena).exists())

    def test_el_contratista_no_responde_los_tramites_finales(self):
        self._hasta_el_cierre()
        self.client.force_login(self.usuarios["contratista"])
        for tipo in (SIIF, SECOP):
            r = self.client.post(
                reverse("cuentas_cobro:tramite",
                        kwargs={"pk": self.cuenta.pk, "tipo": tipo}),
                {"realizado": "on", "comentario": "yo mismo",
                 "evidencia": _archivo()},
            )
            self.assertIn(r.status_code, (403, 404), f"{tipo}: {r.status_code}")
        self.assertEqual(self.cuenta.tramites_finales.count(), 0)

    def test_secop_no_responde_el_tramite_de_siifweb(self):
        """Cada trámite final tiene su rol, y no se cruzan."""
        self._hasta_el_cierre()
        self.client.force_login(self.usuarios["secop"])
        r = self.client.post(
            reverse("cuentas_cobro:tramite",
                    kwargs={"pk": self.cuenta.pk, "tipo": SIIF}),
            {"realizado": "on", "comentario": "me adelanto",
             "evidencia": _archivo()},
        )
        self.assertIn(r.status_code, (400, 403, 404))
        self.assertFalse(self.cuenta.tramites_finales.filter(tipo=SIIF).exists())

    def test_ningun_rol_provoca_un_500_en_ninguna_ruta(self):
        """El barrido ancho: once roles contra todas las rutas de acción.

        Las peticiones se ejecutan de verdad, así que unas van cambiando el
        estado bajo las siguientes. Es deliberado: la matriz recorre también
        combinaciones de estado que ninguna prueba del camino bueno visita.
        """
        malos = []
        for quien, u in self.usuarios.items():
            self.client.force_login(u)
            for metodo, nombre, ruta in self.urls():
                r = self.client.generic(metodo, ruta)
                if r.status_code not in ACEPTABLES:
                    malos.append((quien, nombre, metodo, r.status_code))
            self.client.logout()
        self.assertEqual(malos, [])

    def test_el_superusuario_tampoco_rompe_nada(self):
        self.client.force_login(User.objects.create_superuser("jefe", password="x"))
        malos = []
        for metodo, nombre, ruta in self.urls():
            r = self.client.generic(metodo, ruta)
            if r.status_code not in ACEPTABLES:
                malos.append((nombre, metodo, r.status_code))
        self.assertEqual(malos, [])


class IdsBasuraTest(EscenarioCompleto):
    """Lo que pasa cuando alguien edita el número de la URL."""

    def test_toda_ruta_con_ids_inexistentes_responde_sin_romperse(self):
        self.client.force_login(self.usuarios["supervisor"])
        malos = []
        for metodo, nombre, kwargs in self.rutas():
            if not kwargs:
                continue
            falsos = {
                k: (999999 if isinstance(v, int) else v) for k, v in kwargs.items()
            }
            r = self.client.generic(metodo, reverse(nombre, kwargs=falsos))
            if r.status_code not in ACEPTABLES:
                malos.append((nombre, metodo, r.status_code))
        self.assertEqual(malos, [])

    def test_un_cero_o_un_numero_enorme_en_el_detalle(self):
        self.client.force_login(self.usuarios["supervisor"])
        malos = []
        for nombre in ("cuentas_cobro:cuenta_detalle", "web:entrega_detalle",
                       "web:actividad_detalle", "web:proyecto_detalle",
                       "web:documento_descargar"):
            for valor in (0, 999999, 2 ** 31):
                r = self.client.get(reverse(nombre, kwargs={"pk": valor}))
                if r.status_code not in ACEPTABLES:
                    malos.append((nombre, valor, r.status_code))
        self.assertEqual(malos, [])

    def test_un_id_que_no_es_numero_no_resuelve(self):
        """`<int:pk>` no casa con texto: es un 404 del enrutador, no un 500."""
        self.client.force_login(self.usuarios["supervisor"])
        for ruta in ("/cuentas-cobro/abc/", "/entregas/-1/", "/actividades/1.5/",
                     "/proyectos/%20/"):
            self.assertEqual(self.client.get(ruta).status_code, 404, ruta)

    def test_un_tipo_de_archivo_inventado(self):
        self.client.force_login(self.usuarios["contratista"])
        ruta = reverse("cuentas_cobro:descargar", kwargs={
            "pk": self.cuenta.pk, "tipo": "inventado", "doc_pk": self.doc_cc.pk})
        self.assertEqual(self.client.get(ruta).status_code, 403)

    def test_un_destino_de_subida_inventado(self):
        self.client.force_login(self.usuarios["contratista"])
        ruta = reverse("cuentas_cobro:subida_firmar", kwargs={
            "pk": self.cuenta.pk, "destino": "inventado"})
        self.assertIn(self.client.post(ruta).status_code, (400, 403, 404))

    def test_un_tipo_de_tramite_inventado(self):
        self.client.force_login(self.usuarios["rev_ad"])
        ruta = reverse("cuentas_cobro:tramite", kwargs={
            "pk": self.cuenta.pk, "tipo": "XX"})
        self.assertIn(self.client.post(ruta).status_code, (400, 403, 404))

    def test_un_documento_de_otra_cuenta_no_se_descarga_desde_la_mia(self):
        """El id del documento se valida CONTRA la cuenta de la URL."""
        otra = cc.crear_cuenta(self.usuarios["contratista"], self.vigencia, 7, "julio")
        ajeno = cc.adjuntar_documento(cc.ultima_entrega(otra), self.t1, _archivo())
        self.client.force_login(self.usuarios["contratista"])
        ruta = reverse("cuentas_cobro:descargar", kwargs={
            "pk": self.cuenta.pk, "tipo": "documento", "doc_pk": ajeno.pk})
        self.assertEqual(self.client.get(ruta).status_code, 404)

    def test_un_soporte_de_otra_entrega_no_se_descarga_ni_se_quita(self):
        otra_act = Actividades.objects.create(
            proyecto=self.proyecto, nombre="otra", fecha_programada=timezone.now(),
            fecha_vencimiento=timezone.now() + timedelta(days=2),
            estado=Actividades.EstadoActividad.PENDIENTE,
            asignado_por=self.usuarios["dir"], asignado_a=self.usuarios["form"],
        )
        otra = web_services.crear_entrega(otra_act, self.usuarios["form"], "v1")
        suyo = AdjuntoDeRevision.objects.create(
            content_type=ContentType.objects.get_for_model(otra),
            object_id=otra.pk, usuario=self.usuarios["coord"],
            nombre="ajeno", archivo=_archivo("a.pdf"),
        )
        self.client.force_login(self.usuarios["contratista"])
        for nombre in ("web:adjunto_revision_descargar",
                       "web:adjunto_revision_eliminar"):
            r = self.client.generic(
                "POST" if "eliminar" in nombre else "GET",
                reverse(nombre, kwargs={"pk": suyo.pk}),
            )
            self.assertIn(r.status_code, (403, 404), nombre)
        self.assertTrue(AdjuntoDeRevision.objects.filter(pk=suyo.pk).exists())


class DobleClicTest(EscenarioCompleto):
    """Pulsar dos veces no puede duplicar un hecho ni reventar la aplicación."""

    def _dos_veces(self, usuario, nombre, kwargs, datos=None):
        self.client.force_login(usuario)
        ruta = reverse(nombre, kwargs=kwargs)
        respuestas = (
            self.client.post(ruta, datos or {}),
            self.client.post(ruta, datos or {}),
        )
        for r in respuestas:
            self.assertIn(r.status_code, ACEPTABLES, f"{nombre}: {r.status_code}")
        return respuestas

    def test_entregar_dos_veces_registra_un_solo_envio(self):
        # La cuenta del escenario ya está entregada: esto es el segundo clic.
        antes = self.cuenta.eventos.filter(evento=cc.Eventos.ENVIADO).count()
        self._dos_veces(self.usuarios["contratista"], "cuentas_cobro:entregar",
                        {"pk": self.cuenta.pk})
        self.assertEqual(
            self.cuenta.eventos.filter(evento=cc.Eventos.ENVIADO).count(), antes,
            "la entrega se registró más de una vez",
        )

    def test_radicar_dos_veces_deja_una_sola_decision(self):
        self._aprobar_documentos()
        self._dos_veces(
            self.usuarios["radicador"], "cuentas_cobro:radicacion",
            {"pk": self.cuenta.pk}, {"resultado": "AP", "comentario": "ok"},
        )
        self.assertEqual(self.cuenta.revisionpararadicacion_set.count(), 1)

    def test_asignar_revisores_dos_veces_no_deja_asignaciones_de_sobra(self):
        self._radicar()
        datos = {
            "revisor_tecnico": self.usuarios["rev_te"].pk,
            "revisor_juridico": self.usuarios["rev_ju"].pk,
            "revisor_administrativo": self.usuarios["rev_ad"].pk,
        }
        self._dos_veces(self.usuarios["supervisor"], "cuentas_cobro:asignar",
                        {"pk": self.cuenta.pk}, datos)
        activas = list(self.cuenta.asignacionrevisor_set.filter(
            estado=AsignacionRevisor.Estado.ACTIVA
        ))
        self.assertLessEqual(
            len(activas), 3, "quedó más de una asignación activa por rol"
        )
        self.assertEqual(
            len({a.rol for a in activas}), len(activas),
            "dos asignaciones activas para el mismo rol",
        )

    def test_revisar_dos_veces_la_misma_asignacion(self):
        self._asignar()
        a_te = self.asignaciones[AsignacionRevisor.Rol.TECNICO]
        self._dos_veces(
            self.usuarios["rev_te"], "cuentas_cobro:revisar",
            {"asignacion_pk": a_te.pk}, {"resultado": "AP", "comentario": "ok"},
        )
        self.assertEqual(
            RevisionCuentaCobro.objects.filter(asignacion=a_te).count(), 1,
            "el revisor firmó dos veces la misma versión",
        )

    def test_decidir_dos_veces_como_supervisor(self):
        self._asignar()
        for rol in (AsignacionRevisor.Rol.TECNICO, AsignacionRevisor.Rol.JURIDICO,
                    AsignacionRevisor.Rol.ADMINISTRATIVO):
            cc.registrar_revision(
                self.asignaciones[rol],
                RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok",
            )
        self.cuenta.refresh_from_db()
        self._dos_veces(
            self.usuarios["supervisor"], "cuentas_cobro:decision",
            {"pk": self.cuenta.pk}, {"resultado": "AP", "comentario": "ok"},
        )
        self.assertEqual(
            self.cuenta.eventos.filter(evento=cc.Eventos.SUP_APROBADO).count(), 1
        )

    def test_responder_el_mismo_tramite_dos_veces(self):
        """El doble clic en SIIFWEB no puede dejar dos trámites ni un 5xx."""
        self._hasta_el_cierre()
        self.client.force_login(self.usuarios["rev_ad"])
        ruta = reverse("cuentas_cobro:tramite",
                       kwargs={"pk": self.cuenta.pk, "tipo": SIIF})
        self.client.post(ruta, {
            "realizado": "on", "comentario": "cargado", "evidencia": _archivo()})
        segunda = self.client.post(ruta, {
            "realizado": "on", "comentario": "otra vez", "evidencia": _archivo()})
        self.assertIn(segunda.status_code, ACEPTABLES)
        self.assertEqual(
            self.cuenta.tramites_finales.filter(tipo=SIIF).count(), 1,
            "el trámite quedó registrado dos veces",
        )

    def test_el_segundo_tramite_cierra_la_cuenta_una_sola_vez(self):
        self._hasta_el_cierre()
        cc.responder_tramite(
            self.cuenta, SIIF, self.usuarios["rev_ad"], True, _archivo(), "siif"
        )
        self.cuenta.refresh_from_db()
        self.client.force_login(self.usuarios["secop"])
        ruta = reverse("cuentas_cobro:tramite",
                       kwargs={"pk": self.cuenta.pk, "tipo": SECOP})
        self.client.post(ruta, {
            "realizado": "on", "comentario": "secop", "evidencia": _archivo()})
        segunda = self.client.post(ruta, {
            "realizado": "on", "comentario": "otra vez", "evidencia": _archivo()})
        self.assertIn(segunda.status_code, ACEPTABLES)
        self.cuenta.refresh_from_db()
        self.assertIsNotNone(self.cuenta.fecha_cierre, "la cuenta no se cerró")
        self.assertEqual(
            self.cuenta.eventos.filter(evento=cc.Eventos.CERRADO).count(), 1
        )

    def test_una_cuenta_cerrada_no_admite_nada_mas(self):
        self._hasta_el_cierre()
        cc.responder_tramite(
            self.cuenta, SIIF, self.usuarios["rev_ad"], True, _archivo(), "siif"
        )
        cc.responder_tramite(
            self.cuenta, SECOP, self.usuarios["secop"], True, _archivo(), "secop"
        )
        self.cuenta.refresh_from_db()
        self.assertIsNotNone(self.cuenta.fecha_cierre)
        malos = []
        for quien, u in self.usuarios.items():
            self.client.force_login(u)
            for metodo, nombre, ruta in self.urls():
                if metodo != "POST" or not nombre.startswith("cuentas_cobro:"):
                    continue
                r = self.client.generic(metodo, ruta)
                if r.status_code not in ACEPTABLES:
                    malos.append((quien, nombre, r.status_code))
            self.client.logout()
        self.assertEqual(malos, [])
        self.assertEqual(self.cuenta.tramites_finales.count(), 2)

    def test_realizar_entrega_dos_veces_en_proyectos(self):
        act = Actividades.objects.create(
            proyecto=self.proyecto, nombre="otra",
            fecha_programada=timezone.now(),
            fecha_vencimiento=timezone.now() + timedelta(days=2),
            estado=Actividades.EstadoActividad.PENDIENTE,
            asignado_por=self.usuarios["dir"], asignado_a=self.usuarios["form"],
        )
        e = web_services.crear_entrega(act, self.usuarios["form"], "v1")
        Documentos.objects.create(
            actividad_entrega=e, nombre="d", archivo=_archivo()
        )
        self._dos_veces(self.usuarios["form"], "web:entrega_realizar", {"pk": e.pk})
        act.refresh_from_db()
        self.assertEqual(act.estado, Actividades.EstadoActividad.EN_REVISION)

    def test_abrir_dos_versiones_a_la_vez_en_proyectos(self):
        """Dos clics en Nueva entrega no pueden dejar dos borradores."""
        self._dos_veces(self.usuarios["form"], "web:entrega_nueva",
                        {"actividad_pk": self.actividad.pk}, {"comentario": "v2"})
        self.assertEqual(
            self.actividad.actividadentrega_set.count(), 1,
            "se abrió una segunda versión con la primera en revisión",
        )

    def test_revisar_dos_veces_en_proyectos(self):
        self._dos_veces(
            self.usuarios["coord"], "web:revision_nueva",
            {"entrega_pk": self.entrega.pk},
            {"resultado": "AJ", "comentario": "corrige"},
        )
        self.assertEqual(
            Revisiones.objects.filter(actividad_entrega=self.entrega).count(), 1,
            "la entrega quedó revisada dos veces",
        )

    def test_eliminar_el_mismo_documento_dos_veces(self):
        act = Actividades.objects.create(
            proyecto=self.proyecto, nombre="borrador",
            fecha_programada=timezone.now(),
            fecha_vencimiento=timezone.now() + timedelta(days=2),
            estado=Actividades.EstadoActividad.PENDIENTE,
            asignado_por=self.usuarios["dir"], asignado_a=self.usuarios["form"],
        )
        e = web_services.crear_entrega(act, self.usuarios["form"], "v1")
        doc = Documentos.objects.create(
            actividad_entrega=e, nombre="d", archivo=_archivo()
        )
        self._dos_veces(self.usuarios["form"], "web:documento_eliminar",
                        {"pk": doc.pk})
        self.assertFalse(Documentos.objects.filter(pk=doc.pk).exists())

    def test_crear_la_misma_cuenta_dos_veces(self):
        """Una sola cuenta por contratista, vigencia y mes."""
        self.client.force_login(self.usuarios["contratista"])
        datos = {"vigencia": self.vigencia.pk, "mes": 8, "comentario": "agosto"}
        ruta = reverse("cuentas_cobro:cuenta_nueva")
        self.client.post(ruta, datos)
        self.client.post(ruta, datos)
        self.assertEqual(
            CuentaEntrega.objects.filter(
                usuario=self.usuarios["contratista"], vigencia=self.vigencia, mes=8
            ).count(),
            1,
        )


class SesionPerdidaTest(EscenarioCompleto):
    """Sesiones mal cerradas, que es el caso real de un equipo compartido."""

    def test_un_post_tras_cerrar_sesion_no_ejecuta_nada(self):
        self.client.force_login(self.usuarios["radicador"])
        self.client.post(reverse("cuentas:logout"))
        self._aprobar_documentos()
        r = self.client.post(
            reverse("cuentas_cobro:radicacion", kwargs={"pk": self.cuenta.pk}),
            {"resultado": "AP", "comentario": "sin sesión"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            self.cuenta.revisionpararadicacion_set.count(), 0,
            "se radicó una cuenta sin sesión",
        )

    def test_cambiar_de_usuario_no_hereda_permisos(self):
        self.client.force_login(self.usuarios["supervisor"])
        self.client.get(reverse("cuentas_cobro:cuenta_detalle",
                                kwargs={"pk": self.cuenta.pk}))
        self.client.post(reverse("cuentas:logout"))
        self.client.force_login(self.usuarios["form"])
        r = self.client.get(reverse("cuentas_cobro:cuenta_detalle",
                                    kwargs={"pk": self.cuenta.pk}))
        self.assertIn(r.status_code, (403, 404))

    def test_un_usuario_desactivado_no_entra(self):
        """Desactivar es la salida recomendada para quien ya trabajó."""
        u = self.usuarios["contratista"]
        self.client.force_login(u)
        u.is_active = False
        u.save(update_fields=["is_active"])
        self.assertEqual(
            self.client.get(reverse("cuentas_cobro:bandeja")).status_code, 302
        )

    def test_quitarle_el_rol_cierra_la_seccion_en_la_peticion_siguiente(self):
        u = self.usuarios["contratista"]
        self.client.force_login(u)
        self.assertEqual(
            self.client.get(reverse("cuentas_cobro:bandeja")).status_code, 200
        )
        u.groups.clear()
        self.assertEqual(
            self.client.get(reverse("cuentas_cobro:bandeja")).status_code, 403
        )

    def test_las_paginas_de_la_aplicacion_no_quedan_en_el_navegador(self):
        """Lo que impide que el botón atrás muestre la sesión anterior."""
        self.client.force_login(self.usuarios["contratista"])
        r = self.client.get(reverse("cuentas_cobro:bandeja"))
        self.assertIn("no-store", r["Cache-Control"])


class SinTokenCsrfTest(EscenarioCompleto):
    """Ninguna acción se ejecuta sin el token del formulario.

    El cliente de pruebas se lo salta por omisión, así que sin una prueba que
    lo exija explícitamente un `csrf_exempt` (o un endpoint de subida escrito
    como API) pasaría desapercibido. Hoy no hay ninguno, y esto es lo que lo
    mantiene así.
    """

    def test_ningun_post_pasa_sin_token(self):
        cliente = Client(enforce_csrf_checks=True)
        malos = []
        for quien in ("contratista", "supervisor", "radicador", "coord", "form"):
            cliente.force_login(self.usuarios[quien])
            for metodo, nombre, ruta in self.urls():
                if metodo != "POST":
                    continue
                r = cliente.post(ruta)
                if r.status_code != 403:
                    malos.append((quien, nombre, r.status_code))
            cliente.logout()
        self.assertEqual(malos, [], "una acción se ejecutó sin token CSRF")


class CoberturaDeRutasTest(EscenarioCompleto):
    """Que el barrido no se quede atrás cuando se añada una ruta."""

    def test_toda_url_de_los_dos_dominios_esta_en_la_matriz(self):
        from cuentas_de_cobro import urls as cc_urls
        from web import urls as web_urls

        declaradas = set()
        for modulo, prefijo in ((web_urls, "web"), (cc_urls, "cuentas_cobro")):
            for patron in modulo.urlpatterns:
                declaradas.add(f"{prefijo}:{patron.name}")
        cubiertas = {nombre for _, nombre, _ in self.rutas()}
        self.assertEqual(
            sorted(declaradas - cubiertas), [],
            "rutas sin cubrir: añádelas a EscenarioCompleto.rutas()",
        )
