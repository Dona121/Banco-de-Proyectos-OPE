"""Pruebas de la capa de servicios del módulo de cuentas de cobro."""
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from cuentas_de_cobro import selectors, services
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

Rol = RevisionCuentaCobro.Rol
ResRev = RevisionCuentaCobro.ResultadoRevision
ResRad = CuentaEntrega.ResultadoRevision          # APROBADA / RECHAZADA (estado de la cuenta)
ResRadic = RevisionParaRadicacion.ResultadoRevision  # APROBADA / AJUSTES / RECHAZADA
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
        # Nuevo orden del gating: técnico → jurídico → administrativo.
        services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        services.registrar_revision(a_ju, ResRev.APROBADA, "ok")
        services.registrar_revision(a_ad, ResRev.APROBADA, "ok")
        return a_ju, a_ad, a_te

    def _rechazar_un_doc(self, cuenta):
        """Rechaza el primer documento de la última entrega (habilita devolver)."""
        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(
            doc, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO, "corrige"
        )

    def _recargar_y_entregar(self, cuenta):
        """El contratista recarga el paquete de la versión vigente y la entrega."""
        entrega = services.ultima_entrega(cuenta)
        services.adjuntar_documento(entrega, self.t1, _archivo())
        services.adjuntar_documento(entrega, self.t2, _archivo())
        services.entregar(cuenta, self.contratista)


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
        self.assertIsNotNone(cuenta.fecha_aprobacion_supervisor)

        # Radicación carga los documentos de cierre firmados (mismos tipos del catálogo).
        services.cargar_documento_cierre(cuenta, self.t1, _archivo(), self.radicador)
        services.cargar_documento_cierre(cuenta, self.t2, _archivo(), self.radicador)
        # Trámites finales secuenciales por su rol; el segundo auto-cierra.
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
    def test_juridica_no_arranca_sin_tecnica(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)

        # El técnico va primero; el jurídico no puede arrancar antes de él.
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_ju, ResRev.APROBADA, "ok")

        services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        services.registrar_revision(a_ju, ResRev.APROBADA, "ok")
        services.registrar_revision(a_ad, ResRev.APROBADA, "ok")
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado_revisores, ResRad.APROBADA)

    def test_administrativa_va_de_ultima(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)
        services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        # Aún falta la jurídica: la administrativa (última) no puede arrancar.
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_ad, ResRev.APROBADA, "ok")


class DeclinacionReasignacionTest(FlujoBaseTest):
    def test_declinar_y_reasignar(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        # El técnico es el primero en turno, así que puede revisar sin depender de otro.
        a_te = services.asignar_revisor(cuenta, Rol.TECNICO, self.rev_te, self.supervisor)

        services.declinar_asignacion(a_te, "vacaciones")
        a_te.refresh_from_db()
        self.assertEqual(a_te.estado, AsignacionRevisor.Estado.DECLINADA)

        with self.assertRaises(ValidationError):
            services.registrar_revision(a_te, ResRev.APROBADA, "ok")

        nueva = services.reasignar(cuenta, Rol.TECNICO, self.rev_ad, self.supervisor)
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
        # Al rechazar no se registra fecha de aprobación del supervisor.
        self.assertIsNone(cuenta.fecha_aprobacion_supervisor)

    def test_aprobacion_supervisor_registra_fecha(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        services.decidir_supervisor(cuenta, self.supervisor, ResRad.APROBADA, "ok")
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado_supervisor, ResRad.APROBADA)
        self.assertIsNotNone(cuenta.fecha_aprobacion_supervisor)


class RevisionAprobacionSegunDocsTest(FlujoBaseTest):
    def test_no_aprueba_revision_con_documento_sin_resolver(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        entrega = services.ultima_entrega(cuenta)
        doc = entrega.documentoscuentacobro_set.first()
        Estado = DocumentosCuentaCobro.EstadoDocumento
        # El técnico (primero en turno) marca un documento rechazado: no puede aprobar.
        services.revisar_documento(doc, Estado.RECHAZADO, "corrige")
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        # Lo resuelve como "No aplica" → ahora todos están AP/NA y sí puede aprobar.
        services.revisar_documento(doc, Estado.NO_APLICA, "no aplica")
        services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        self.assertTrue(
            entrega.revisioncuentacobro_set.filter(rol=Rol.TECNICO).exists()
        )


class MarcadoDocumentosTest(FlujoBaseTest):
    def test_revisor_de_turno_marca_documentos_observados(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._asignar_todos(cuenta)
        self.assertTrue(selectors.puede_marcar_documentos(self.rev_te, cuenta))
        self.assertFalse(selectors.puede_marcar_documentos(self.rev_ju, cuenta))

        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(doc, doc.EstadoDocumento.RECHAZADO, "corrige esto")
        doc.refresh_from_db()
        self.assertEqual(doc.estado, doc.EstadoDocumento.RECHAZADO)
        self.assertEqual(doc.comentario, "corrige esto")


class ReinicioTotalTest(FlujoBaseTest):
    def test_devolucion_genera_version_vacia_y_exige_reentrega(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        # Devolución del técnico (primero): requiere rechazar un documento.
        self._rechazar_un_doc(cuenta)
        services.registrar_revision(a_te, ResRev.AJUSTES, "corrige")

        entrega2 = services.ultima_entrega(cuenta)
        self.assertEqual(entrega2.numero_version, 2)
        self.assertEqual(entrega2.revisioncuentacobro_set.count(), 0)
        # La v2 nace vacía y SIN entregar: aún nadie puede revisarla.
        self.assertFalse(services.rol_habilitado(entrega2, Rol.TECNICO))
        # Tras recargar y reentregar, el técnico (primero) queda habilitado.
        self._recargar_y_entregar(cuenta)
        self.assertTrue(services.rol_habilitado(entrega2, Rol.TECNICO))
        self.assertFalse(services.rol_habilitado(entrega2, Rol.JURIDICO))

    def test_ajustes_en_juridico_reinicia_desde_tecnico(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)
        services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        self._rechazar_un_doc(cuenta)
        services.registrar_revision(a_ju, ResRev.AJUSTES, "corrige")

        entrega2 = services.ultima_entrega(cuenta)
        self.assertEqual(entrega2.revisioncuentacobro_set.count(), 0)
        # Reinicia desde el técnico, pero solo tras la reentrega del contratista.
        self.assertFalse(services.rol_habilitado(entrega2, Rol.TECNICO))
        self._recargar_y_entregar(cuenta)
        self.assertTrue(services.rol_habilitado(entrega2, Rol.TECNICO))
        self.assertFalse(services.rol_habilitado(entrega2, Rol.JURIDICO))

    def test_no_se_puede_aprobar_la_version_vacia_tras_devolucion(self):
        # Escenario reportado: tras devolver, el revisor NO debe poder aprobar la
        # versión nueva vacía sin que el contratista reentregue.
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        self._rechazar_un_doc(cuenta)
        services.registrar_revision(a_te, ResRev.AJUSTES, "corrige")
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_te, ResRev.APROBADA, "ok")

    def test_form_de_revision_oculto_hasta_reentrega(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        self._rechazar_un_doc(cuenta)
        services.registrar_revision(a_te, ResRev.AJUSTES, "corrige")
        # El botón/form de revisar se controla con selectors.puede_revisar.
        a_te.refresh_from_db()
        self.assertFalse(selectors.puede_revisar(self.rev_te, a_te))
        self._recargar_y_entregar(cuenta)
        self.assertTrue(selectors.puede_revisar(self.rev_te, a_te))


class TramitesFinalesTest(FlujoBaseTest):
    def _hasta_supervisor(self):
        """Hasta la aprobación del supervisor (sin cargar el cierre todavía)."""
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        services.decidir_supervisor(cuenta, self.supervisor, ResRad.APROBADA, "ok")
        cuenta.refresh_from_db()
        return cuenta

    def _hasta_cierre(self):
        cuenta = self._hasta_supervisor()
        services.cargar_documento_cierre(cuenta, self.t1, _archivo(), self.radicador)
        services.cargar_documento_cierre(cuenta, self.t2, _archivo(), self.radicador)
        cuenta.refresh_from_db()
        return cuenta

    def test_cierre_lo_carga_radicacion_no_el_contratista(self):
        cuenta = self._hasta_supervisor()
        self.assertTrue(selectors.puede_cargar_cierre(self.radicador, cuenta))
        self.assertFalse(selectors.puede_cargar_cierre(self.contratista, cuenta))
        with self.assertRaises(ValidationError):
            services.cargar_documento_cierre(cuenta, self.t1, _archivo(), self.contratista)

    def test_completitud_cierre_contra_obligatorios_de_vigencia(self):
        cuenta = self._hasta_supervisor()
        # Faltan ambos obligatorios.
        self.assertEqual(
            {t.id for t in services.documentos_cierre_faltantes(cuenta)},
            {self.t1.id, self.t2.id},
        )
        services.cargar_documento_cierre(cuenta, self.t1, _archivo(), self.radicador)
        self.assertEqual(
            [t.id for t in services.documentos_cierre_faltantes(cuenta)], [self.t2.id]
        )
        services.cargar_documento_cierre(cuenta, self.t2, _archivo(), self.radicador)
        self.assertEqual(services.documentos_cierre_faltantes(cuenta), [])

    def test_siif_requiere_cierre_completo(self):
        cuenta = self._hasta_supervisor()
        # Sin cierre completo, SIIFWEB no se habilita.
        self.assertFalse(services.tramite_habilitado(cuenta, Tipo.CARGUE_SIIFWEB))
        services.cargar_documento_cierre(cuenta, self.t1, _archivo(), self.radicador)
        services.cargar_documento_cierre(cuenta, self.t2, _archivo(), self.radicador)
        self.assertTrue(services.tramite_habilitado(cuenta, Tipo.CARGUE_SIIFWEB))

    def test_secuencia_secop_requiere_siif(self):
        cuenta = self._hasta_cierre()
        self.assertTrue(services.tramite_habilitado(cuenta, Tipo.CARGUE_SIIFWEB))
        self.assertFalse(services.tramite_habilitado(cuenta, Tipo.CARGUE_SECOP))
        with self.assertRaises(ValidationError):
            services.responder_tramite(
                cuenta, Tipo.CARGUE_SECOP, self.secop, True, _archivo(), "x")

    def test_cada_tramite_solo_por_su_rol(self):
        cuenta = self._hasta_cierre()
        # SIIFWEB: solo el revisor administrativo. SECOP II: solo el rol de secop.
        self.assertTrue(selectors.puede_responder_tramite(self.rev_ad, cuenta, Tipo.CARGUE_SIIFWEB))
        self.assertFalse(selectors.puede_responder_tramite(self.secop, cuenta, Tipo.CARGUE_SIIFWEB))
        # Tras SIIFWEB, SECOP II solo lo responde secop.
        services.responder_tramite(cuenta, Tipo.CARGUE_SIIFWEB, self.rev_ad, True, _archivo(), "siif")
        self.assertTrue(selectors.puede_responder_tramite(self.secop, cuenta, Tipo.CARGUE_SECOP))
        self.assertFalse(selectors.puede_responder_tramite(self.rev_ad, cuenta, Tipo.CARGUE_SECOP))

    def test_evidencia_exige_realizado(self):
        cuenta = self._hasta_cierre()
        with self.assertRaises(ValidationError):
            services.responder_tramite(
                cuenta, Tipo.CARGUE_SIIFWEB, self.rev_ad, True, None, "sin evidencia")


class RadicacionCoherenteConDocumentosTest(FlujoBaseTest):
    """En la radicación (primera revisión), devolver/rechazar exige al menos un
    documento rechazado; aprobar exige que todos estén AP/NA."""

    def _entregada(self):
        cuenta = self._cuenta_con_documentos()
        self._entregar(cuenta)
        return cuenta

    def _aprobar_todos_los_docs(self, cuenta):
        for d in services.ultima_entrega(cuenta).documentoscuentacobro_set.all():
            services.revisar_documento(d, d.EstadoDocumento.APROBADO)

    def test_no_devuelve_si_ningun_documento_esta_rechazado(self):
        cuenta = self._entregada()
        self._aprobar_todos_los_docs(cuenta)
        with self.assertRaises(ValidationError):
            services.registrar_revision_radicacion(
                cuenta, self.supervisor, ResRadic.AJUSTES, "corrige")

    def test_no_rechaza_si_ningun_documento_esta_rechazado(self):
        cuenta = self._entregada()
        self._aprobar_todos_los_docs(cuenta)
        with self.assertRaises(ValidationError):
            services.registrar_revision_radicacion(
                cuenta, self.supervisor, ResRad.RECHAZADA, "no cumple")

    def test_devuelve_si_hay_documento_rechazado(self):
        cuenta = self._entregada()
        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(doc, doc.EstadoDocumento.RECHAZADO, "corrige")
        services.registrar_revision_radicacion(
            cuenta, self.supervisor, ResRadic.AJUSTES, "corrige")
        cuenta.refresh_from_db()
        # Devolución → versión nueva vacía y sigue sin radicar.
        self.assertIsNone(cuenta.fecha_radicacion)
        self.assertEqual(services.ultima_entrega(cuenta).numero_version, 2)


class RadicacionRechazoTerminalTest(FlujoBaseTest):
    """El rechazo en radicación es terminal: la cuenta queda rechazada, no admite
    más decisiones y no vuelve a notificar."""

    def _entregada_con_doc_rechazado(self):
        cuenta = self._cuenta_con_documentos()
        self._entregar(cuenta)
        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(doc, doc.EstadoDocumento.RECHAZADO, "no corresponde")
        return cuenta

    def test_rechazo_es_terminal(self):
        cuenta = self._entregada_con_doc_rechazado()
        services.registrar_revision_radicacion(
            cuenta, self.supervisor, ResRadic.RECHAZADA, "rechazada")
        cuenta.refresh_from_db()
        self.assertTrue(services.radicacion_rechazada(cuenta))
        self.assertIsNone(cuenta.fecha_radicacion)
        # Ya no se puede volver a decidir la radicación (el form desaparece).
        self.assertFalse(selectors.puede_radicar(self.supervisor, cuenta))
        self.assertFalse(selectors.puede_radicar(self.radicador, cuenta))
        # Se muestra como rechazada en el paso actual y en el stepper.
        self.assertTrue(services.paso_actual(cuenta)["rechazada"])
        etapa = next(
            e for e in services.flujo_de_cuenta(cuenta) if e["clave"] == "radicacion"
        )
        self.assertEqual(etapa["estado"], "rechazada")

    def test_rechazo_no_deja_notificacion_de_radicacion(self):
        cuenta = self._entregada_con_doc_rechazado()
        services.registrar_revision_radicacion(
            cuenta, self.supervisor, ResRadic.RECHAZADA, "rechazada")
        textos = [n["texto"] for n in services.notificaciones_para(self.radicador)]
        self.assertFalse(any("aprobación de radicación" in t for t in textos))


class RevisionCoherenteConDocumentosTest(FlujoBaseTest):
    """La revisión debe ser coherente con el estado de los documentos:
    aprobar exige todos AP/NA; devolver exige al menos uno rechazado."""

    def test_no_devuelve_si_ningun_documento_esta_rechazado(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        # Todos los documentos quedaron aprobados en la radicación: no hay nada que
        # corregir, así que "requiere ajustes" debe fallar.
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_te, ResRev.AJUSTES, "corrige")

    def test_devuelve_si_hay_documento_rechazado(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        self._rechazar_un_doc(cuenta)
        services.registrar_revision(a_te, ResRev.AJUSTES, "corrige")
        self.assertEqual(services.ultima_entrega(cuenta).numero_version, 2)

    def test_no_aprueba_si_hay_documento_rechazado(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        self._rechazar_un_doc(cuenta)
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_te, ResRev.APROBADA, "ok")


class RevisionTresRevisoresTest(FlujoBaseTest):
    """Los dos bugs no deben reaparecer con NINGUNO de los tres revisores
    (técnico, jurídico, administrativo), no solo con el primero del gating."""

    ROLES = (Rol.TECNICO, Rol.JURIDICO, Rol.ADMINISTRATIVO)

    def _avanzar_hasta(self, cuenta, objetivo):
        """Asigna los tres revisores y aprueba los roles previos a ``objetivo``
        según el orden del gating. Devuelve la asignación del rol objetivo."""
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)
        por_rol = {Rol.JURIDICO: a_ju, Rol.ADMINISTRATIVO: a_ad, Rol.TECNICO: a_te}
        for rol in services.SECUENCIA_ROLES:
            if rol == objetivo:
                break
            services.registrar_revision(por_rol[rol], ResRev.APROBADA, "ok")
        return por_rol[objetivo]

    def test_ningun_revisor_devuelve_sin_documento_rechazado(self):
        for objetivo in self.ROLES:
            with self.subTest(rol=objetivo):
                cuenta = self._radicar(self._cuenta_con_documentos())
                asignacion = self._avanzar_hasta(cuenta, objetivo)
                with self.assertRaises(ValidationError):
                    services.registrar_revision(asignacion, ResRev.AJUSTES, "corrige")

    def test_ningun_revisor_aprueba_con_documento_rechazado(self):
        for objetivo in self.ROLES:
            with self.subTest(rol=objetivo):
                cuenta = self._radicar(self._cuenta_con_documentos())
                asignacion = self._avanzar_hasta(cuenta, objetivo)
                self._rechazar_un_doc(cuenta)
                with self.assertRaises(ValidationError):
                    services.registrar_revision(asignacion, ResRev.APROBADA, "ok")

    def test_devolucion_de_cualquier_revisor_exige_reentrega(self):
        for objetivo in self.ROLES:
            with self.subTest(rol=objetivo):
                cuenta = self._radicar(self._cuenta_con_documentos())
                asignacion = self._avanzar_hasta(cuenta, objetivo)
                self._rechazar_un_doc(cuenta)
                services.registrar_revision(asignacion, ResRev.AJUSTES, "corrige")
                entrega2 = services.ultima_entrega(cuenta)
                # v2 vacía y sin reentregar: NINGÚN rol puede revisarla.
                for rol in self.ROLES:
                    self.assertFalse(services.rol_habilitado(entrega2, rol))
                # Tras la reentrega, el ciclo reinicia en el técnico (primero).
                self._recargar_y_entregar(cuenta)
                self.assertTrue(services.rol_habilitado(entrega2, Rol.TECNICO))
                self.assertFalse(services.rol_habilitado(entrega2, Rol.JURIDICO))
                self.assertFalse(services.rol_habilitado(entrega2, Rol.ADMINISTRATIVO))


class RevisorSinRechazoTest(FlujoBaseTest):
    """El revisor solo puede aprobar o devolver (requiere ajustes); el rechazo
    definitivo (RE) no aplica en la etapa de revisión."""

    def test_revisor_no_puede_rechazar(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        with self.assertRaises(ValidationError):
            services.registrar_revision(a_te, ResRev.RECHAZADA, "no")

    def test_formulario_solo_ofrece_aprobar_o_ajustes(self):
        from cuentas_de_cobro.forms import RevisionForm
        codigos = {c for c, _ in RevisionForm().fields["resultado"].choices}
        self.assertEqual(codigos, {ResRev.APROBADA, ResRev.AJUSTES})
        self.assertNotIn(ResRev.RECHAZADA, codigos)


class TrazabilidadTest(FlujoBaseTest):
    def test_eventos_y_marca_de_devolucion(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        _, _, a_te = self._asignar_todos(cuenta)
        self._rechazar_un_doc(cuenta)
        services.registrar_revision(a_te, ResRev.AJUSTES, "corrige")

        eventos = list(cuenta.eventos.values_list("evento", flat=True))
        self.assertIn(services.Eventos.ENVIADO, eventos)
        self.assertIn(services.Eventos.RAD_APROBADA, eventos)
        self.assertIn(services.Eventos.NUEVA_VERSION, eventos)
        self.assertTrue(services.es_devolucion(services.Eventos.devolucion_de_revisor(Rol.TECNICO)))


class NotificacionesCuentasTest(FlujoBaseTest):
    """Notificaciones derivadas (sin modelo) que calcula ``notificaciones_para``."""

    def _textos(self, user):
        return [n["texto"] for n in services.notificaciones_para(user)]

    def test_contratista_pendiente_de_entregar(self):
        self._cuenta_con_documentos()  # v1, aún sin "Entregar"
        self.assertTrue(
            any("entrega tus documentos" in t for t in self._textos(self.contratista))
        )

    def test_revisor_en_turno_recibe_pendiente(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._asignar_todos(cuenta)
        # El técnico va primero: le llega la notificación; al jurídico aún no.
        self.assertTrue(any("revisión pendiente" in t for t in self._textos(self.rev_te)))
        self.assertFalse(any("revisión pendiente" in t for t in self._textos(self.rev_ju)))

    def test_supervisor_espera_decision_final(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        self.assertTrue(any("decisión final" in t for t in self._textos(self.supervisor)))


class CuentasVisiblesScopeTest(FlujoBaseTest):
    """Alcance de ``cuentas_visibles``: cada rol ve solo lo que le corresponde."""

    def test_contratista_ve_solo_las_suyas(self):
        cuenta = self._cuenta_con_documentos()
        otro = User.objects.create_user("contra2", password="x")
        otro.groups.add(Group.objects.get(name="Contratista"))
        self.assertIn(cuenta, selectors.cuentas_visibles(self.contratista))
        self.assertEqual(list(selectors.cuentas_visibles(otro)), [])

    def test_revisor_ve_solo_donde_esta_asignado(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self.assertNotIn(cuenta, selectors.cuentas_visibles(self.rev_te))
        self._asignar_todos(cuenta)
        self.assertIn(cuenta, selectors.cuentas_visibles(self.rev_te))

    def test_usuario_sin_rol_no_ve_nada(self):
        self._cuenta_con_documentos()
        sin_rol = User.objects.create_user("nadie", password="x")
        self.assertEqual(list(selectors.cuentas_visibles(sin_rol)), [])
        self.assertFalse(services.es_devolucion(services.Eventos.RAD_APROBADA))
