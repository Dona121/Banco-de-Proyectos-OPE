"""Pruebas de la capa de servicios del módulo de cuentas de cobro."""
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from cuentas_de_cobro import selectors, services
from cuentas_de_cobro.models import (
    AsignacionRevisor,
    CuentaEntrega,
    DocumentoCierre,
    RequisitoDocumental,
    RevisionCuentaCobro,
    TipoDocumentoCargue,
    TramiteFinal,
    Vigencia,
)

Rol = RevisionCuentaCobro.Rol
ResRev = RevisionCuentaCobro.ResultadoRevision
ResRad = CuentaEntrega.ResultadoRevision
Tipo = TramiteFinal.Tipo


def _archivo(nombre="doc.pdf"):
    return SimpleUploadedFile(nombre, b"contenido", content_type="application/pdf")


class FlujoBaseTest(TestCase):
    def setUp(self):
        self.contratista = User.objects.create_user("contra", password="x")
        self.supervisor = User.objects.create_user("super", password="x")
        self.radicador = User.objects.create_user("radi", password="x")
        self.secop = User.objects.create_user("secop", password="x")
        self.rev_ju = User.objects.create_user("ju", password="x")
        self.rev_ad = User.objects.create_user("ad", password="x")
        self.rev_te = User.objects.create_user("te", password="x")
        self.contratista.groups.add(Group.objects.get(name="Contratista"))
        self.supervisor.groups.add(Group.objects.get(name="Supervisor"))
        self.radicador.groups.add(Group.objects.get(name="Radicacion"))
        self.secop.groups.add(Group.objects.get(name="Secop"))
        for u in (self.rev_ju, self.rev_ad, self.rev_te):
            u.groups.add(Group.objects.get(name="Revisor"))

        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.t1 = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        self.t2 = TipoDocumentoCargue.objects.create(nombre="Planilla")
        RequisitoDocumental.objects.create(vigencia=self.vigencia, tipo_documento=self.t1)
        RequisitoDocumental.objects.create(vigencia=self.vigencia, tipo_documento=self.t2)

    # -- helpers -------------------------------------------------------------- #
    def _cuenta_con_documentos(self):
        cuenta = services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")
        entrega = services.ultima_entrega(cuenta)
        services.adjuntar_documento(entrega, self.t1, _archivo())
        services.adjuntar_documento(entrega, self.t2, _archivo())
        return cuenta

    def _entregar(self, cuenta):
        services.entregar(cuenta, self.contratista)

    def _radicar(self, cuenta):
        self._entregar(cuenta)
        for d in services.ultima_entrega(cuenta).documentoscuentacobro_set.all():
            services.revisar_documento(d, d.EstadoDocumento.APROBADO)
        services.registrar_revision_radicacion(
            cuenta, self.supervisor, ResRad.APROBADA, "ok"
        )
        cuenta.refresh_from_db()
        return cuenta

    def _asignar_todos(self, cuenta):
        a_ju = services.asignar_revisor(cuenta, Rol.JURIDICO, self.rev_ju, self.supervisor)
        a_ad = services.asignar_revisor(cuenta, Rol.ADMINISTRATIVO, self.rev_ad, self.supervisor)
        a_te = services.asignar_revisor(cuenta, Rol.TECNICO, self.rev_te, self.supervisor)
        return a_ju, a_ad, a_te

    def _aprobar_revisores(self, cuenta):
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)
        services.registrar_revision(a_ju, ResRev.APROBADA, "ok")
        services.registrar_revision(a_ad, ResRev.APROBADA, "ok")
        services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        return a_ju, a_ad, a_te


class CaminoFelizTest(FlujoBaseTest):
    def test_flujo_completo_end_to_end(self):
        cuenta = self._cuenta_con_documentos()
        self.assertEqual(services.documentos_faltantes(cuenta), [])

        self._radicar(cuenta)
        self.assertIsNotNone(cuenta.fecha_radicacion)

        self._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado_revisores, ResRad.APROBADA)
        self.assertIsNotNone(cuenta.fecha_aprobacion_revisores)

        services.decidir_supervisor(cuenta, self.supervisor, ResRad.APROBADA, "aprobada")
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado_supervisor, ResRad.APROBADA)

        # El contratista carga los documentos de cierre.
        services.cargar_documento_cierre(
            cuenta, DocumentoCierre.Tipo.INFORME_SUPERVISION, _archivo(), self.contratista
        )
        services.cargar_documento_cierre(
            cuenta, DocumentoCierre.Tipo.CERTIFICADO_CUMPLIMIENTO, _archivo(), self.contratista
        )
        # Trámites finales secuenciales por su rol; el tercero auto-cierra.
        services.responder_tramite(
            cuenta, TramiteFinal.Tipo.ENTREGA_CIERRE, self.radicador, True, _archivo(), "entregado")
        services.responder_tramite(
            cuenta, TramiteFinal.Tipo.CARGUE_SIIFWEB, self.rev_ad, True, _archivo(), "siif")
        services.responder_tramite(
            cuenta, TramiteFinal.Tipo.CARGUE_SECOP, self.secop, True, _archivo(), "secop")

        cuenta.refresh_from_db()
        self.assertIsNotNone(cuenta.fecha_cierre)

    def test_entregar_bloqueado_si_faltan_documentos(self):
        cuenta = services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")
        entrega = services.ultima_entrega(cuenta)
        services.adjuntar_documento(entrega, self.t1, _archivo())  # falta t2
        with self.assertRaises(ValidationError):
            services.entregar(cuenta, self.contratista)
        self.assertFalse(services.entrega_enviada(cuenta))

    def test_radicacion_requiere_entrega(self):
        cuenta = self._cuenta_con_documentos()
        # Sin "Entregar", el supervisor no puede radicar.
        with self.assertRaises(ValidationError):
            services.registrar_revision_radicacion(
                cuenta, self.supervisor, ResRad.APROBADA, "ok")


class GatingTest(FlujoBaseTest):
    def test_tecnica_no_arranca_sin_administrativa(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)

        services.registrar_revision(a_ju, ResRev.APROBADA, "ok")
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_te, ResRev.APROBADA, "ok")

        services.registrar_revision(a_ad, ResRev.APROBADA, "ok")
        services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado_revisores, ResRad.APROBADA)

    def test_administrativa_no_arranca_sin_juridica(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, a_ad, _ = self._asignar_todos(cuenta)
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_ad, ResRev.APROBADA, "ok")


class DeclinacionReasignacionTest(FlujoBaseTest):
    def test_declinar_y_reasignar(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju = services.asignar_revisor(cuenta, Rol.JURIDICO, self.rev_ju, self.supervisor)

        services.declinar_asignacion(a_ju, "vacaciones")
        a_ju.refresh_from_db()
        self.assertEqual(a_ju.estado, AsignacionRevisor.Estado.DECLINADA)

        with self.assertRaises(ValidationError):
            services.registrar_revision(a_ju, ResRev.APROBADA, "ok")

        nueva = services.reasignar(cuenta, Rol.JURIDICO, self.rev_ad, self.supervisor)
        self.assertEqual(nueva.estado, AsignacionRevisor.Estado.ACTIVA)
        services.registrar_revision(nueva, ResRev.APROBADA, "ok")
        self.assertEqual(
            services.ultima_entrega(cuenta).revisioncuentacobro_set.count(), 1
        )

    def test_no_dos_activas_por_rol(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        services.asignar_revisor(cuenta, Rol.JURIDICO, self.rev_ju, self.supervisor)
        with self.assertRaises(ValidationError):
            services.asignar_revisor(cuenta, Rol.JURIDICO, self.rev_ad, self.supervisor)


class RechazoSupervisorTest(FlujoBaseTest):
    def test_supervisor_no_decide_antes_de_revisores(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        with self.assertRaises(ValidationError):
            services.decidir_supervisor(cuenta, self.supervisor, ResRad.APROBADA, "x")

    def test_rechazo_del_supervisor(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        services.decidir_supervisor(cuenta, self.supervisor, ResRad.RECHAZADA, "no cumple")
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado_supervisor, ResRad.RECHAZADA)
        self.assertEqual(cuenta.estado_revisores, ResRad.APROBADA)


class MarcadoDocumentosTest(FlujoBaseTest):
    def test_revisor_de_turno_marca_documentos_observados(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._asignar_todos(cuenta)
        self.assertTrue(selectors.puede_marcar_documentos(self.rev_ju, cuenta))
        self.assertFalse(selectors.puede_marcar_documentos(self.rev_ad, cuenta))

        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(doc, doc.EstadoDocumento.RECHAZADO, "corrige esto")
        doc.refresh_from_db()
        self.assertEqual(doc.estado, doc.EstadoDocumento.RECHAZADO)
        self.assertEqual(doc.comentario, "corrige esto")


class ReinicioTotalTest(FlujoBaseTest):
    def test_devolucion_genera_version_vacia_reinicia_en_juridico(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, _, _ = self._asignar_todos(cuenta)
        # Devolución del jurídico → el sistema genera versión nueva automáticamente.
        services.registrar_revision(a_ju, ResRev.AJUSTES, "corrige")

        entrega2 = services.ultima_entrega(cuenta)
        self.assertEqual(entrega2.numero_version, 2)
        self.assertEqual(entrega2.revisioncuentacobro_set.count(), 0)
        self.assertTrue(services.rol_habilitado(entrega2, Rol.JURIDICO))
        self.assertFalse(services.rol_habilitado(entrega2, Rol.ADMINISTRATIVO))

    def test_ajustes_en_administrativo_reinicia_desde_juridico(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)
        services.registrar_revision(a_ju, ResRev.APROBADA, "ok")
        services.registrar_revision(a_ad, ResRev.AJUSTES, "corrige")

        entrega2 = services.ultima_entrega(cuenta)
        self.assertEqual(entrega2.revisioncuentacobro_set.count(), 0)
        self.assertTrue(services.rol_habilitado(entrega2, Rol.JURIDICO))
        self.assertFalse(services.rol_habilitado(entrega2, Rol.ADMINISTRATIVO))
        self.assertFalse(services.rol_habilitado(entrega2, Rol.TECNICO))


class TramitesFinalesTest(FlujoBaseTest):
    def _hasta_cierre(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        services.decidir_supervisor(cuenta, self.supervisor, ResRad.APROBADA, "ok")
        cuenta.refresh_from_db()
        services.cargar_documento_cierre(
            cuenta, DocumentoCierre.Tipo.INFORME_SUPERVISION, _archivo(), self.contratista)
        services.cargar_documento_cierre(
            cuenta, DocumentoCierre.Tipo.CERTIFICADO_CUMPLIMIENTO, _archivo(), self.contratista)
        cuenta.refresh_from_db()
        return cuenta

    def test_secuencia_siif_requiere_entrega(self):
        cuenta = self._hasta_cierre()
        self.assertTrue(services.tramite_habilitado(cuenta, Tipo.ENTREGA_CIERRE))
        self.assertFalse(services.tramite_habilitado(cuenta, Tipo.CARGUE_SIIFWEB))
        with self.assertRaises(ValidationError):
            services.responder_tramite(
                cuenta, Tipo.CARGUE_SIIFWEB, self.rev_ad, True, _archivo(), "x")

    def test_cada_tramite_solo_por_su_rol(self):
        cuenta = self._hasta_cierre()
        # El secop NO puede responder el trámite de entrega (rol radicación).
        self.assertFalse(selectors.puede_responder_tramite(self.secop, cuenta, Tipo.ENTREGA_CIERRE))
        self.assertTrue(selectors.puede_responder_tramite(self.radicador, cuenta, Tipo.ENTREGA_CIERRE))

    def test_evidencia_exige_realizado(self):
        cuenta = self._hasta_cierre()
        with self.assertRaises(ValidationError):
            services.responder_tramite(
                cuenta, Tipo.ENTREGA_CIERRE, self.radicador, True, None, "sin evidencia")


class TrazabilidadTest(FlujoBaseTest):
    def test_eventos_y_marca_de_devolucion(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, _, _ = self._asignar_todos(cuenta)
        services.registrar_revision(a_ju, ResRev.AJUSTES, "corrige")

        eventos = list(cuenta.eventos.values_list("evento", flat=True))
        self.assertIn(services.Eventos.ENVIADO, eventos)
        self.assertIn(services.Eventos.RAD_APROBADA, eventos)
        self.assertIn(services.Eventos.NUEVA_VERSION, eventos)
        self.assertTrue(services.es_devolucion(services.Eventos.devolucion_de_revisor(Rol.JURIDICO)))
        self.assertFalse(services.es_devolucion(services.Eventos.RAD_APROBADA))
