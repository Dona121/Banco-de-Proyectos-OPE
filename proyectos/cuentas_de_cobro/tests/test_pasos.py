"""Caracterización del paso actual y del flujograma, etapa por etapa.

`flujo_de_cuenta` alimenta el stepper del detalle y `paso_actual` la columna
"paso / responsable" de la bandeja. Estas pruebas fijan su resultado EXACTO en
cada punto del flujo (incluidos los bordes: devolución, rechazo en radicación,
rechazo del supervisor y cuenta cerrada) para poder optimizar el cálculo sin
cambiar lo que ve el usuario.
"""
from cuentas_de_cobro import services
from cuentas_de_cobro.models import CuentaEntrega, RevisionCuentaCobro, TramiteFinal

from .test_services import FlujoBaseTest, _archivo

Rol = RevisionCuentaCobro.Rol
ResRev = RevisionCuentaCobro.ResultadoRevision
Tipo = TramiteFinal.Tipo

CLAVES = [
    "entrega", "radicacion", "asignacion", "revision",
    "supervisor", "cierre_docs", "tramites", "cierre",
]


class PasosDelFlujoTest(FlujoBaseTest):
    # -- helpers -------------------------------------------------------------- #
    def _estados(self, cuenta):
        """{clave de etapa: estado} tal como lo pinta el stepper."""
        flujo = services.flujo_de_cuenta(cuenta)
        self.assertEqual([e["clave"] for e in flujo], CLAVES)
        return {e["clave"]: e["estado"] for e in flujo}

    def _detalle(self, cuenta, clave):
        return next(
            e["detalle"] for e in services.flujo_de_cuenta(cuenta) if e["clave"] == clave
        )

    def _paso(self, cuenta):
        p = services.paso_actual(cuenta)
        return (p["titulo"], p["responsable"], p["siguiente"], p["siguiente_responsable"])

    def _cargar_cierre(self, cuenta):
        for tipo in services.tipos_obligatorios(cuenta):
            services.cargar_documento_cierre(cuenta, tipo, _archivo(), self.radicador)

    # -- 1. recién creada ----------------------------------------------------- #
    def test_recien_creada(self):
        cuenta = self._cuenta_con_documentos()
        self.assertEqual(self._estados(cuenta), {
            "entrega": services.ACTUAL, "radicacion": services.FUTURA,
            "asignacion": services.FUTURA, "revision": services.FUTURA,
            "supervisor": services.FUTURA, "cierre_docs": services.FUTURA,
            "tramites": services.FUTURA, "cierre": services.FUTURA,
        })
        self.assertEqual(self._detalle(cuenta, "entrega"), "Versión 1 en preparación")
        self.assertEqual(self._paso(cuenta), (
            "Cargue y entrega", "Contratista",
            "Radicación", "Supervisor o rol de radicación",
        ))

    # -- 2. entregada, esperando radicación ----------------------------------- #
    def test_entregada(self):
        cuenta = self._cuenta_con_documentos()
        self._entregar(cuenta)
        estados = self._estados(cuenta)
        self.assertEqual(estados["entrega"], services.HECHA)
        self.assertEqual(estados["radicacion"], services.ACTUAL)
        self.assertEqual(self._detalle(cuenta, "entrega"), "Versión 1 entregada")
        self.assertEqual(self._detalle(cuenta, "radicacion"), "Esperando aprobación")
        self.assertEqual(self._paso(cuenta), (
            "Radicación", "Supervisor o rol de radicación",
            "Asignación de revisores", "Supervisor",
        ))

    # -- 3. radicada, sin revisores asignados --------------------------------- #
    def test_radicada_sin_revisores(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        estados = self._estados(cuenta)
        self.assertEqual(estados["radicacion"], services.HECHA)
        self.assertEqual(estados["asignacion"], services.ACTUAL)
        self.assertEqual(estados["revision"], services.FUTURA)
        self.assertEqual(self._detalle(cuenta, "asignacion"), "Pendiente de asignar")
        self.assertEqual(self._paso(cuenta), (
            "Asignación de revisores", "Supervisor",
            "Revisión (técnico → jurídico → administrativo)", "Revisor técnico",
        ))

    # -- 4. en revisión: el turno avanza por la secuencia --------------------- #
    def test_revision_turno_tecnico_y_luego_juridico(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)
        estados = self._estados(cuenta)
        self.assertEqual(estados["asignacion"], services.HECHA)
        self.assertEqual(estados["revision"], services.ACTUAL)
        self.assertEqual(self._detalle(cuenta, "revision"), "En revisión técnico")
        self.assertEqual(self._paso(cuenta), (
            "Revisión (técnico → jurídico → administrativo)", "Revisor técnico",
            "Decisión del supervisor (para firma)", "Supervisor",
        ))

        services.registrar_revision(a_te, ResRev.APROBADA, "ok")
        self.assertEqual(self._detalle(cuenta, "revision"), "En revisión jurídico")
        self.assertEqual(self._paso(cuenta)[1], "Revisor jurídico")

        services.registrar_revision(a_ju, ResRev.APROBADA, "ok")
        self.assertEqual(self._detalle(cuenta, "revision"), "En revisión administrativo")
        self.assertEqual(self._paso(cuenta)[1], "Revisor administrativo")

    # -- 5. los tres aprobaron: decide el supervisor -------------------------- #
    def test_revisores_aprobaron(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        estados = self._estados(cuenta)
        self.assertEqual(estados["revision"], services.HECHA)
        self.assertEqual(estados["supervisor"], services.ACTUAL)
        self.assertEqual(self._detalle(cuenta, "revision"), "Los tres roles aprobaron")
        self.assertEqual(self._paso(cuenta), (
            "Decisión del supervisor (para firma)", "Supervisor",
            "Documentos de cierre firmados", "Rol de radicación",
        ))

    # -- 6. aprobada para firma: carga el cierre radicación ------------------- #
    def test_aprobada_por_supervisor(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        services.decidir_supervisor(
            cuenta, self.supervisor, CuentaEntrega.ResultadoRevision.APROBADA, "ok"
        )
        cuenta.refresh_from_db()
        estados = self._estados(cuenta)
        self.assertEqual(estados["supervisor"], services.HECHA)
        self.assertEqual(estados["cierre_docs"], services.ACTUAL)
        self.assertEqual(self._detalle(cuenta, "cierre_docs"), "Cargue incompleto")
        self.assertEqual(self._paso(cuenta), (
            "Documentos de cierre firmados", "Rol de radicación",
            "Trámites finales (SIIFWEB → SECOP II)", "Revisor administrativo",
        ))

    # -- 7. cierre cargado: arrancan los trámites finales --------------------- #
    def test_cierre_cargado_y_tramites(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        services.decidir_supervisor(
            cuenta, self.supervisor, CuentaEntrega.ResultadoRevision.APROBADA, "ok"
        )
        cuenta.refresh_from_db()
        self._cargar_cierre(cuenta)
        estados = self._estados(cuenta)
        self.assertEqual(estados["cierre_docs"], services.HECHA)
        self.assertEqual(estados["tramites"], services.ACTUAL)
        self.assertEqual(self._detalle(cuenta, "tramites"), "Realizados: ninguno")
        self.assertEqual(self._paso(cuenta), (
            "Trámites finales (SIIFWEB → SECOP II)", "Revisor administrativo",
            "Cierre", "Sistema (automático)",
        ))

        services.responder_tramite(
            cuenta, Tipo.CARGUE_SIIFWEB, self.rev_ad, True, _archivo(), "ok"
        )
        cuenta.refresh_from_db()
        self.assertEqual(self._detalle(cuenta, "tramites"), "Realizados: Cargue en SIIFWEB")
        self.assertEqual(self._paso(cuenta)[1], "Rol de secop")

    # -- 8. cerrada ----------------------------------------------------------- #
    def test_cuenta_cerrada(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        services.decidir_supervisor(
            cuenta, self.supervisor, CuentaEntrega.ResultadoRevision.APROBADA, "ok"
        )
        cuenta.refresh_from_db()
        self._cargar_cierre(cuenta)
        services.responder_tramite(
            cuenta, Tipo.CARGUE_SIIFWEB, self.rev_ad, True, _archivo(), "ok"
        )
        cuenta.refresh_from_db()
        services.responder_tramite(
            cuenta, Tipo.CARGUE_SECOP, self.secop, True, _archivo(), "ok"
        )
        cuenta.refresh_from_db()
        self.assertIsNotNone(cuenta.fecha_cierre)
        estados = self._estados(cuenta)
        self.assertEqual(estados["tramites"], services.HECHA)
        self.assertEqual(estados["cierre"], services.HECHA)
        paso = services.paso_actual(cuenta)
        self.assertTrue(paso["cerrada"])
        self.assertFalse(paso["rechazada"])
        self.assertEqual(paso["titulo"], "Cerrada")
        self.assertEqual(paso["desde"], cuenta.fecha_cierre)

    # -- 9. devolución de un revisor: vuelve al contratista ------------------- #
    def test_devolucion_de_revisor(self):
        """Tras la devolución la pelota es del contratista, y así debe leerse.

        Nace la versión 2 vacía y sin entregar: el cargue vuelve a estar en curso
        y la revisión vuelve a esperar su turno, aunque la cuenta siga radicada y
        con los tres revisores asignados. El paso siguiente salta la radicación
        (que ya ocurrió) y nombra al revisor técnico, porque el ciclo reinicia
        desde él.
        """
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)
        self._rechazar_un_doc(cuenta)
        services.registrar_revision(a_te, ResRev.AJUSTES, "corrige")
        cuenta.refresh_from_db()

        entrega = services.ultima_entrega(cuenta)
        self.assertEqual(entrega.numero_version, 2)
        self.assertFalse(services.entrega_enviada(cuenta))
        self.assertEqual(entrega.documentoscuentacobro_set.count(), 0)

        estados = self._estados(cuenta)
        self.assertEqual(estados["entrega"], services.ACTUAL)
        self.assertEqual(estados["radicacion"], services.HECHA)
        self.assertEqual(estados["asignacion"], services.HECHA)
        self.assertEqual(estados["revision"], services.FUTURA)
        self.assertEqual(
            self._detalle(cuenta, "entrega"),
            "En corrección por devolución (versión 2)",
        )
        self.assertEqual(self._paso(cuenta), (
            "Cargue y entrega", "Contratista",
            "Revisión (técnico → jurídico → administrativo)", "Revisor técnico",
        ))

    def test_tras_reentregar_vuelve_a_mandar_el_revisor_tecnico(self):
        """Cerrado el círculo: al reentregar, el paso actual es la revisión."""
        cuenta = self._radicar(self._cuenta_con_documentos())
        a_ju, a_ad, a_te = self._asignar_todos(cuenta)
        self._rechazar_un_doc(cuenta)
        services.registrar_revision(a_te, ResRev.AJUSTES, "corrige")
        cuenta.refresh_from_db()
        self._recargar_y_entregar(cuenta)
        cuenta.refresh_from_db()

        estados = self._estados(cuenta)
        self.assertEqual(estados["entrega"], services.HECHA)
        self.assertEqual(estados["revision"], services.ACTUAL)
        self.assertEqual(self._detalle(cuenta, "entrega"), "Versión 2 entregada")
        self.assertEqual(self._detalle(cuenta, "revision"), "En revisión técnico")
        self.assertEqual(self._paso(cuenta), (
            "Revisión (técnico → jurídico → administrativo)", "Revisor técnico",
            "Decisión del supervisor (para firma)", "Supervisor",
        ))

    def test_devolucion_en_radicacion_si_vuelve_a_radicacion(self):
        """Contraste con la devolución de revisor: aquí la cuenta nunca se radicó,
        así que el paso siguiente sí es la radicación. El "siguiente" se deduce de
        qué etapas faltan, no de la posición en la lista."""
        cuenta = self._cuenta_con_documentos()
        self._entregar(cuenta)
        self._rechazar_un_doc(cuenta)
        services.registrar_revision_radicacion(
            cuenta, self.supervisor,
            cuenta.revisionpararadicacion_set.model.ResultadoRevision.AJUSTES,
            "corrige",
        )
        cuenta.refresh_from_db()

        estados = self._estados(cuenta)
        self.assertEqual(estados["entrega"], services.ACTUAL)
        self.assertEqual(estados["radicacion"], services.FUTURA)
        self.assertEqual(
            self._detalle(cuenta, "entrega"),
            "En corrección por devolución (versión 2)",
        )
        self.assertEqual(self._paso(cuenta), (
            "Cargue y entrega", "Contratista",
            "Radicación", "Supervisor o rol de radicación",
        ))

    # -- 10. rechazo terminal en radicación ----------------------------------- #
    def test_rechazada_en_radicacion(self):
        cuenta = self._cuenta_con_documentos()
        self._entregar(cuenta)
        self._rechazar_un_doc(cuenta)
        services.registrar_revision_radicacion(
            cuenta, self.supervisor,
            cuenta.revisionpararadicacion_set.model.ResultadoRevision.RECHAZADA,
            "no continúa",
        )
        cuenta.refresh_from_db()
        self.assertEqual(self._estados(cuenta)["radicacion"], services.RECHAZADA)
        self.assertEqual(self._detalle(cuenta, "radicacion"), "Rechazada")
        paso = services.paso_actual(cuenta)
        self.assertTrue(paso["rechazada"])
        self.assertFalse(paso["cerrada"])
        self.assertEqual(paso["titulo"], "Rechazada en radicación")
        self.assertEqual(paso["responsable"], "-")

    # -- 11. rechazo del supervisor ------------------------------------------- #
    def test_rechazada_por_el_supervisor(self):
        cuenta = self._radicar(self._cuenta_con_documentos())
        self._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        services.decidir_supervisor(
            cuenta, self.supervisor, CuentaEntrega.ResultadoRevision.RECHAZADA, "no"
        )
        cuenta.refresh_from_db()
        self.assertEqual(self._estados(cuenta)["supervisor"], services.RECHAZADA)
        paso = services.paso_actual(cuenta)
        self.assertTrue(paso["rechazada"])
        self.assertEqual(paso["titulo"], "Rechazada por el supervisor")
