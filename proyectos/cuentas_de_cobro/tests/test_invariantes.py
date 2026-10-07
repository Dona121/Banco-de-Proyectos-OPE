"""Invariantes del módulo de cuentas de cobro, atacados por la vía de servicio.

Mismo criterio que `web/test_invariantes.py`: saltarse la vista y atacar el
servicio, que es donde deben estar las reglas. El módulo aguantó casi todo; lo
único que cedió fue el mes de la cuenta, que admitía valores inexistentes
porque `CuentaEntrega` no llama a `full_clean()` y las `choices` de Django son
validación de formulario, no restricción de base.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase

from cuentas_de_cobro import selectors, services
from cuentas_de_cobro.models import (
    AsignacionRevisor,
    CuentaEntrega,
    DocumentosCuentaCobro,
    RevisionCuentaCobro,
)

from .test_services import FlujoBaseTest


class Base(TestCase):
    def setUp(self):
        self.base = FlujoBaseTest("run")
        self.base.setUp()

    def _entregada(self):
        return self.base._cuenta_con_documentos()

    def _radicada(self):
        """Cuenta radicada y con los tres revisores ya asignados."""
        c = self.base._radicar(self._entregada())
        self.base._asignar_todos(c)
        c.refresh_from_db()
        return c


class EntregaSondeo(Base):
    def test_no_se_entrega_dos_veces(self):
        c = self._entregada()
        services.entregar(c, self.base.contratista)
        with self.assertRaises(ValidationError):
            services.entregar(c, self.base.contratista)

    def test_no_se_entrega_sin_los_obligatorios(self):
        c = services.crear_cuenta(
            self.base.contratista, self.base.vigencia, 7, "julio"
        )
        with self.assertRaises(ValidationError):
            services.entregar(c, self.base.contratista)

    def test_no_se_adjunta_a_una_version_ya_entregada(self):
        c = self._entregada()
        services.entregar(c, self.base.contratista)
        tipo = services.tipos_obligatorios(c)[0]
        with self.assertRaises(ValidationError):
            services.adjuntar_documento(
                services.ultima_entrega(c), tipo, self.base._archivo_de_prueba()
            )


class RadicacionSondeo(Base):
    def test_no_se_radica_dos_veces(self):
        c = self._radicada()
        with self.assertRaises(ValidationError):
            services.registrar_revision_radicacion(
                c, self.base.radicador, c.ResultadoRevision.APROBADA, "otra vez"
            )

    def test_no_se_radica_sin_entregar(self):
        c = self._entregada()  # cargada pero sin pulsar Entregar
        with self.assertRaises(ValidationError):
            services.registrar_revision_radicacion(
                c, self.base.radicador, c.ResultadoRevision.APROBADA, "ok"
            )

    def test_tras_rechazo_definitivo_no_continua(self):
        c = self._entregada()
        services.entregar(c, self.base.contratista)
        doc = DocumentosCuentaCobro.objects.filter(
            documento_entrega__cuenta_entrega=c
        ).first()
        services.revisar_documento(
            doc, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO,
            "no sirve", self.base.radicador,
        )
        services.registrar_revision_radicacion(
            c, self.base.radicador, c.ResultadoRevision.RECHAZADA, "rechazada"
        )
        c.refresh_from_db()
        self.assertTrue(services.radicacion_rechazada(c))
        self.assertFalse(selectors.puede_radicar(self.base.radicador, c))
        with self.assertRaises(ValidationError):
            services.registrar_revision_radicacion(
                c, self.base.radicador, c.ResultadoRevision.APROBADA, "me arrepentí"
            )


class RevisionSondeo(Base):
    def _asignacion(self, cuenta, rol):
        return cuenta.asignacionrevisor_set.get(
            rol=rol, estado=AsignacionRevisor.Estado.ACTIVA
        )

    def test_no_se_salta_el_orden_del_gating(self):
        """Ni el administrativo ni el jurídico revisan antes que el técnico."""
        c = self._radicada()
        for rol in (AsignacionRevisor.Rol.ADMINISTRATIVO, AsignacionRevisor.Rol.JURIDICO):
            with self.subTest(rol=rol):
                with self.assertRaises(ValidationError):
                    services.registrar_revision(
                        self._asignacion(c, rol),
                        RevisionCuentaCobro.ResultadoRevision.APROBADA, "me adelanto",
                    )

    def test_un_revisor_no_usa_el_rechazo_terminal(self):
        c = self._radicada()
        with self.assertRaises(ValidationError):
            services.registrar_revision(
                self._asignacion(c, AsignacionRevisor.Rol.TECNICO),
                RevisionCuentaCobro.ResultadoRevision.RECHAZADA, "fuera",
            )

    def test_no_se_aprueba_con_un_documento_pendiente(self):
        """Coherencia: aprobar exige que todos estén AP/NA.

        La radicación ya los deja aprobados, así que para probarlo hay que
        devolver uno a PENDIENTE, que es lo que pasa cuando el revisor aún no lo
        ha mirado.
        """
        c = self._radicada()
        doc = DocumentosCuentaCobro.objects.filter(
            documento_entrega__cuenta_entrega=c
        ).first()
        services.revisar_documento(
            doc, DocumentosCuentaCobro.EstadoDocumento.PENDIENTE, "",
            self.base.rev_te,
        )
        with self.assertRaises(ValidationError):
            services.registrar_revision(
                self._asignacion(c, AsignacionRevisor.Rol.TECNICO),
                RevisionCuentaCobro.ResultadoRevision.APROBADA, "sin mirar",
            )

    def test_una_devolucion_exige_un_documento_rechazado(self):
        """Con todo aprobado, devolver no es una acción válida."""
        c = self._radicada()   # la radicación deja todos los documentos en AP
        with self.assertRaises(ValidationError):
            services.registrar_revision(
                self._asignacion(c, AsignacionRevisor.Rol.TECNICO),
                RevisionCuentaCobro.ResultadoRevision.AJUSTES,
                "devuelvo sin motivo",
            )

    def test_no_se_revisa_dos_veces_el_mismo_rol(self):
        c = self._radicada()
        asignacion = self._asignacion(c, AsignacionRevisor.Rol.TECNICO)
        services.registrar_revision(
            asignacion, RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok"
        )
        with self.assertRaises(ValidationError):
            services.registrar_revision(
                asignacion, RevisionCuentaCobro.ResultadoRevision.APROBADA, "otra vez"
            )


class CuentaSondeo(Base):
    def test_no_se_duplica_el_periodo_en_tramite(self):
        services.crear_cuenta(self.base.contratista, self.base.vigencia, 9, "sep")
        with self.assertRaises(ValidationError):
            services.crear_cuenta(self.base.contratista, self.base.vigencia, 9, "sep")

    def test_un_mes_invalido_no_pasa(self):
        for mes in (0, 13, -1):
            with self.subTest(mes=mes):
                with self.assertRaises(ValidationError):
                    services.crear_cuenta(
                        self.base.contratista, self.base.vigencia, mes, "x"
                    )


class CapasDeValidacionSondeo(Base):
    """Las redes bajo el servicio para la cuenta de cobro."""

    def test_la_senal_rechaza_un_mes_inventado(self):
        """`CuentaEntrega` no llama a full_clean en su save; la señal lo suple."""
        c = self.base._cuenta_con_documentos()
        c.mes = 13
        with self.assertRaises(ValidationError):
            c.save(update_fields=["mes"])

    def test_la_senal_aplica_el_invariante_del_supervisor(self):
        """El supervisor no decide antes de que los revisores aprueben."""
        c = self.base._cuenta_con_documentos()
        c.estado_supervisor = CuentaEntrega.ResultadoRevision.APROBADA
        with self.assertRaises(ValidationError):
            c.save(update_fields=["estado_supervisor"])

    def test_la_restriccion_de_base_resiste_al_sql_crudo(self):
        from django.db import IntegrityError, connection, transaction

        c = self.base._cuenta_con_documentos()
        with self.assertRaises(IntegrityError):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    "UPDATE cuentas_de_cobro_cuentaentrega SET mes = 13 WHERE id = %s",
                    [c.pk],
                )


class BorradoDeDatosDePruebaTest(Base):
    """Retirar una cuenta de prueba con todo su rastro y sus archivos.

    El modelo protege de verdad (`RevisionCuentaCobro.asignacion` es PROTECT y
    nueve claves apuntan a `User` igual), así que el borrado real es explícito
    y acotado: no se gana por accidente.
    """

    def _cuenta_completa(self):
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._aprobar_revisores(c)
        c.refresh_from_db()
        return c

    def test_una_cuenta_no_se_borra_sin_mas(self):
        from django.db.models import ProtectedError

        c = self._cuenta_completa()
        with self.assertRaises(ProtectedError):
            c.delete()

    def test_el_helper_la_borra_con_todo_su_rastro(self):
        from cuentas.borrado import eliminar_cuenta_con_rastro
        from cuentas_de_cobro.models import (
            AsignacionRevisor, CuentaEntrega, DocumentoEntrega,
            DocumentosCuentaCobro, EventoTrazabilidad, RevisionCuentaCobro,
            RevisionParaRadicacion,
        )

        c = self._cuenta_completa()
        pk = c.pk
        self.assertTrue(EventoTrazabilidad.objects.filter(cuenta_entrega_id=pk).exists())

        with self.captureOnCommitCallbacks(execute=True):
            eliminar_cuenta_con_rastro(c)

        self.assertFalse(CuentaEntrega.objects.filter(pk=pk).exists())
        for modelo, campo in (
            (DocumentoEntrega, "cuenta_entrega_id"),
            (AsignacionRevisor, "cuenta_entrega_id"),
            (RevisionParaRadicacion, "cuenta_entrega_id"),
            (EventoTrazabilidad, "cuenta_entrega_id"),
        ):
            self.assertFalse(modelo.objects.filter(**{campo: pk}).exists(), modelo)
        self.assertFalse(DocumentosCuentaCobro.objects.exists())
        self.assertFalse(RevisionCuentaCobro.objects.exists())

    def test_los_archivos_salen_del_almacenamiento(self):
        """Lo que antes se quedaba: el objeto del bucket sobrevivía a la fila."""
        from django.core.files.storage import default_storage

        from cuentas.borrado import eliminar_cuenta_con_rastro
        from cuentas_de_cobro.models import DocumentosCuentaCobro

        c = self._cuenta_completa()
        claves = [
            d.documento.name
            for d in DocumentosCuentaCobro.objects.filter(
                documento_entrega__cuenta_entrega=c
            )
        ]
        self.assertTrue(claves)
        self.assertTrue(all(default_storage.exists(k) for k in claves))

        with self.captureOnCommitCallbacks(execute=True):
            eliminar_cuenta_con_rastro(c)

        for k in claves:
            self.assertFalse(default_storage.exists(k), k)

    def test_el_resumen_avisa_de_lo_que_se_lleva_por_delante(self):
        from cuentas.borrado import resumen_de_borrado

        c = self._cuenta_completa()
        # El supervisor no es el dueño de la cuenta, pero intervino en ella.
        resumen = resumen_de_borrado(self.base.supervisor)
        self.assertIn(c, resumen["cuentas"])
