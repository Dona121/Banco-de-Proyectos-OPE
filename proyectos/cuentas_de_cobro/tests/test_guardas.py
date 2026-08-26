"""Guardas de negocio en la capa de servicios.

Las vistas ya escondían estas acciones en la interfaz, pero la capa de servicios
—que es donde se decide cada transición de estado— las permitía. Un POST directo,
un doble clic o una pestaña vieja bastaban para saltárselas.
"""
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from cuentas_de_cobro import selectors, services
from cuentas_de_cobro.models import (
    CuentaEntrega,
    DocumentosCuentaCobro,
    RequisitoDocumental,
    RevisionParaRadicacion,
    TipoDocumentoCargue,
    Vigencia,
)

# Los FileField van a Supabase por defecto: sin esto, correr las pruebas
# escribiría en el bucket real de producción.
_STORAGES_TEST = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _archivo(nombre="doc.pdf"):
    return SimpleUploadedFile(nombre, b"contenido", content_type="application/pdf")


@override_settings(STORAGES=_STORAGES_TEST)
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

    # -- Una cuenta por contratista, vigencia y mes --------------------------- #
    def test_no_se_puede_duplicar_la_cuenta_del_mismo_periodo(self):
        services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")
        with self.assertRaises(ValidationError):
            services.crear_cuenta(self.contratista, self.vigencia, 6, "junio otra vez")
        self.assertEqual(
            CuentaEntrega.objects.filter(
                usuario=self.contratista, vigencia=self.vigencia, mes=6
            ).count(),
            1,
        )

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
