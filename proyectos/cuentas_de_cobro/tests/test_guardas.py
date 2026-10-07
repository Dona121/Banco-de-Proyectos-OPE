"""Guardas de negocio en la capa de servicios.

Las vistas ya escondían estas acciones en la interfaz, pero la capa de servicios
(que es donde se decide cada transición de estado) las permitía. Un POST directo,
un doble clic o una pestaña vieja bastaban para saltárselas.
"""
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
    Vigencia,
)

# Los FileField van a Supabase por defecto: el storage en memoria y la base
# SQLite los fija `proyectos.test_settings`.
#     uv run python manage.py test --settings=proyectos.test_settings


def _archivo(nombre="doc.pdf"):
    return SimpleUploadedFile(nombre, b"contenido", content_type="application/pdf")


class GuardasTest(TestCase):
    def setUp(self):
        self.contratista = User.objects.create_user("c1", password="x")
        self.contratista.groups.add(Group.objects.get(name="Contratista"))
        self.supervisor = User.objects.create_user("s1", password="x")
        self.supervisor.groups.add(Group.objects.get(name="Supervisor"))
        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.tipo = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        RequisitoDocumental.objects.create(
            vigencia=self.vigencia, tipo_documento=self.tipo
        )

    def _cuenta(self, mes=6):
        cuenta = services.crear_cuenta(self.contratista, self.vigencia, mes, "x")
        services.adjuntar_documento(
            services.ultima_entrega(cuenta), self.tipo, _archivo()
        )
        return cuenta

    def _rechazar_radicacion(self, cuenta):
        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(doc, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO, "no cumple")
        services.registrar_revision_radicacion(
            cuenta, self.supervisor, RevisionParaRadicacion.ResultadoRevision.RECHAZADA,
            "rechazo definitivo",
        )
        return doc

    # -- Una cuenta VIGENTE por contratista, vigencia y mes ------------------- #
    def test_no_se_puede_duplicar_la_cuenta_del_mismo_periodo(self):
        services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")
        with self.assertRaises(ValidationError) as caso:
            services.crear_cuenta(self.contratista, self.vigencia, 6, "junio otra vez")
        self.assertIn("en trámite", " ".join(caso.exception.messages))
        self.assertEqual(
            CuentaEntrega.objects.filter(
                usuario=self.contratista, vigencia=self.vigencia, mes=6
            ).count(),
            1,
        )

    def test_tras_un_rechazo_en_radicacion_se_puede_volver_a_presentar(self):
        """Lo que bloquea es el ESTADO, no la existencia: una cuenta rechazada en
        radicación no impide presentar otra del mismo periodo."""
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        self._rechazar_radicacion(cuenta)
        cuenta.refresh_from_db()
        self.assertTrue(services.cuenta_rechazada(cuenta))

        nueva = services.crear_cuenta(self.contratista, self.vigencia, cuenta.mes, "otra vez")
        self.assertNotEqual(nueva.pk, cuenta.pk)
        self.assertEqual(
            CuentaEntrega.objects.filter(
                usuario=self.contratista, vigencia=self.vigencia, mes=cuenta.mes
            ).count(),
            2,
        )

    def test_tras_un_rechazo_del_supervisor_se_puede_volver_a_presentar(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        for d in services.ultima_entrega(cuenta).documentoscuentacobro_set.all():
            services.revisar_documento(d, d.EstadoDocumento.APROBADO)
        services.registrar_revision_radicacion(
            cuenta, self.supervisor, RevisionParaRadicacion.ResultadoRevision.APROBADA, "ok"
        )
        cuenta.refresh_from_db()
        revisores = []
        for rol in (
            AsignacionRevisor.Rol.TECNICO,
            AsignacionRevisor.Rol.JURIDICO,
            AsignacionRevisor.Rol.ADMINISTRATIVO,
        ):
            revisor = User.objects.create_user(f"rev{rol}", password="x")
            revisor.groups.add(Group.objects.get(name="Revisor"))
            revisores.append(
                services.asignar_revisor(cuenta, rol, revisor, self.supervisor)
            )
        for asignacion in revisores:
            services.registrar_revision(
                asignacion, RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok"
            )
        cuenta.refresh_from_db()
        services.decidir_supervisor(
            cuenta, self.supervisor, CuentaEntrega.ResultadoRevision.RECHAZADA, "no"
        )
        cuenta.refresh_from_db()
        self.assertTrue(services.cuenta_rechazada(cuenta))

        nueva = services.crear_cuenta(self.contratista, self.vigencia, cuenta.mes, "otra vez")
        self.assertNotEqual(nueva.pk, cuenta.pk)

    def test_una_cuenta_aprobada_bloquea_el_periodo(self):
        cuenta = self._cuenta()
        # Atajo: lo que importa aquí es el estado final, no el camino.
        cuenta.estado_revisores = CuentaEntrega.ResultadoRevision.APROBADA
        cuenta.estado_supervisor = CuentaEntrega.ResultadoRevision.APROBADA
        cuenta.save(update_fields=["estado_revisores", "estado_supervisor"])
        self.assertTrue(services.cuenta_aprobada(cuenta))

        with self.assertRaises(ValidationError) as caso:
            services.crear_cuenta(self.contratista, self.vigencia, cuenta.mes, "otra")
        self.assertIn("aprobada", " ".join(caso.exception.messages))

    def test_dos_rechazos_no_bloquean_un_tercer_intento(self):
        primera = self._cuenta()
        services.entregar(primera, self.contratista)
        self._rechazar_radicacion(primera)
        segunda = services.crear_cuenta(
            self.contratista, self.vigencia, primera.mes, "segundo intento"
        )
        services.adjuntar_documento(
            services.ultima_entrega(segunda), self.tipo, _archivo()
        )
        services.entregar(segunda, self.contratista)
        self._rechazar_radicacion(segunda)
        tercera = services.crear_cuenta(
            self.contratista, self.vigencia, primera.mes, "tercer intento"
        )
        self.assertNotIn(tercera.pk, {primera.pk, segunda.pk})

    def test_otro_mes_si_se_permite(self):
        services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")
        services.crear_cuenta(self.contratista, self.vigencia, 7, "julio")
        self.assertEqual(CuentaEntrega.objects.filter(usuario=self.contratista).count(), 2)

    def test_otro_contratista_si_puede_tener_el_mismo_mes(self):
        otro = User.objects.create_user("c2", password="x")
        otro.groups.add(Group.objects.get(name="Contratista"))
        services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")
        services.crear_cuenta(otro, self.vigencia, 6, "junio")
        self.assertEqual(CuentaEntrega.objects.filter(vigencia=self.vigencia, mes=6).count(), 2)

    # -- "Entregar" no se puede pulsar dos veces ------------------------------ #
    def test_no_se_puede_entregar_dos_veces_la_misma_version(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        with self.assertRaises(ValidationError):
            services.entregar(cuenta, self.contratista)
        self.assertEqual(
            cuenta.eventos.filter(evento=services.Eventos.ENVIADO).count(), 1
        )

    def test_tras_devolucion_se_puede_entregar_de_nuevo(self):
        """La guarda no debe bloquear el reintento legítimo: tras una devolución
        nace una versión nueva y el contratista vuelve a entregar."""
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(doc, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO, "corrige")
        services.registrar_revision_radicacion(
            cuenta, self.supervisor, RevisionParaRadicacion.ResultadoRevision.AJUSTES, "ajusta"
        )
        # Versión nueva y vacía: recargar y volver a entregar debe funcionar.
        nueva = services.ultima_entrega(cuenta)
        self.assertEqual(nueva.numero_version, 2)
        services.adjuntar_documento(nueva, self.tipo, _archivo())
        services.entregar(cuenta, self.contratista)
        self.assertEqual(
            cuenta.eventos.filter(evento=services.Eventos.ENVIADO).count(), 2
        )

    def test_no_se_puede_entregar_tras_rechazo_en_radicacion(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        self._rechazar_radicacion(cuenta)
        cuenta.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.entregar(cuenta, self.contratista)

    # -- El rechazo en radicación es terminal --------------------------------- #
    def test_rechazo_en_radicacion_no_admite_nueva_decision(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        doc = self._rechazar_radicacion(cuenta)
        cuenta.refresh_from_db()
        self.assertTrue(services.radicacion_rechazada(cuenta))

        # Aunque se "arreglen" los documentos, la cuenta no se puede radicar.
        services.revisar_documento(doc, DocumentosCuentaCobro.EstadoDocumento.APROBADO, "")
        with self.assertRaises(ValidationError):
            services.registrar_revision_radicacion(
                cuenta, self.supervisor,
                RevisionParaRadicacion.ResultadoRevision.APROBADA, "ahora sí",
            )
        cuenta.refresh_from_db()
        self.assertIsNone(cuenta.fecha_radicacion)

    def test_tras_rechazo_terminal_no_se_marcan_documentos(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        self._rechazar_radicacion(cuenta)
        cuenta.refresh_from_db()
        self.assertFalse(selectors.puede_marcar_documentos(self.supervisor, cuenta))
        self.assertFalse(selectors.puede_radicar(self.supervisor, cuenta))

    def test_no_se_marcan_documentos_antes_de_la_entrega(self):
        """Antes de que el contratista entregue no hay nada que revisar."""
        cuenta = self._cuenta()
        self.assertFalse(selectors.puede_marcar_documentos(self.supervisor, cuenta))
        services.entregar(cuenta, self.contratista)
        cuenta.refresh_from_db()
        self.assertTrue(selectors.puede_marcar_documentos(self.supervisor, cuenta))

    # -- El paquete entregado queda congelado --------------------------------- #
    def _devolver_en_radicacion(self, cuenta):
        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(
            doc, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO, "corrige"
        )
        services.registrar_revision_radicacion(
            cuenta, self.supervisor,
            RevisionParaRadicacion.ResultadoRevision.AJUSTES, "ajusta",
        )

    def test_no_se_carga_documento_en_una_version_ya_entregada(self):
        """Un documento colado tras la entrega entra en PENDIENTE y deja al
        revisor de turno sin acción válida: no puede aprobar ni devolver."""
        opcional = TipoDocumentoCargue.objects.create(nombre="Anexo opcional")
        RequisitoDocumental.objects.create(
            vigencia=self.vigencia, tipo_documento=opcional, obligatorio=False
        )
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        cuenta.refresh_from_db()
        self.assertFalse(selectors.puede_cargar_documentos(self.contratista, cuenta))
        entrega = services.ultima_entrega(cuenta)
        with self.assertRaises(ValidationError):
            services.adjuntar_documento(entrega, opcional, _archivo("extra.pdf"))
        self.assertEqual(entrega.documentoscuentacobro_set.count(), 1)

    def test_tras_devolucion_se_vuelve_a_poder_cargar(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        self._devolver_en_radicacion(cuenta)
        cuenta.refresh_from_db()
        self.assertTrue(selectors.puede_cargar_documentos(self.contratista, cuenta))
        services.adjuntar_documento(
            services.ultima_entrega(cuenta), self.tipo, _archivo()
        )

    def test_no_se_carga_documento_en_una_version_vieja(self):
        cuenta = self._cuenta()
        vieja = services.ultima_entrega(cuenta)
        services.entregar(cuenta, self.contratista)
        self._devolver_en_radicacion(cuenta)
        cuenta.refresh_from_db()
        self.assertEqual(services.ultima_entrega(cuenta).numero_version, 2)
        with self.assertRaises(ValidationError):
            services.adjuntar_documento(vieja, self.tipo, _archivo("otro.pdf"))

    def test_no_se_carga_tras_rechazo_terminal_en_radicacion(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        self._rechazar_radicacion(cuenta)
        cuenta.refresh_from_db()
        self.assertFalse(selectors.puede_cargar_documentos(self.contratista, cuenta))

    # -- La bitácora es de solo lectura --------------------------------------- #
    def test_la_bitacora_no_se_edita_ni_se_borra_desde_el_admin(self):
        """Es el registro de auditoría: si se puede alterar, deja de ser evidencia."""
        from django.contrib import admin as dj_admin

        from cuentas_de_cobro.models import EventoTrazabilidad

        admin_eventos = dj_admin.site._registry[EventoTrazabilidad]
        peticion = type("P", (), {"user": self.supervisor, "method": "GET"})()
        self.assertFalse(admin_eventos.has_add_permission(peticion))
        self.assertFalse(admin_eventos.has_change_permission(peticion))
        self.assertFalse(admin_eventos.has_delete_permission(peticion))
        for campo in ("cuenta_entrega", "etapa", "evento", "actor", "detalle"):
            self.assertIn(campo, admin_eventos.readonly_fields)

    # -- Presentación: el mes en texto, no en número -------------------------- #
    def test_nombre_de_cuenta_usa_el_mes_en_texto(self):
        cuenta = self._cuenta()
        self.assertEqual(str(cuenta), "2026: 6")  # el modelo es definitivo
        self.assertEqual(services.nombre_de_cuenta(cuenta), "2026 · Junio")

    # -- Rechazar un documento exige causal ----------------------------------- #
    def test_rechazar_documento_sin_causal_falla(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        with self.assertRaises(ValidationError):
            services.revisar_documento(
                doc, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO, "   "
            )
        doc.refresh_from_db()
        self.assertEqual(doc.estado, DocumentosCuentaCobro.EstadoDocumento.PENDIENTE)

    def test_aprobar_y_no_aplica_no_exigen_causal(self):
        cuenta = self._cuenta()
        services.entregar(cuenta, self.contratista)
        doc = services.ultima_entrega(cuenta).documentoscuentacobro_set.first()
        services.revisar_documento(doc, DocumentosCuentaCobro.EstadoDocumento.APROBADO)
        services.revisar_documento(doc, DocumentosCuentaCobro.EstadoDocumento.NO_APLICA)
        doc.refresh_from_db()
        self.assertEqual(doc.estado, DocumentosCuentaCobro.EstadoDocumento.NO_APLICA)
