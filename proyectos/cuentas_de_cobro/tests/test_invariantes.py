"""Invariantes del módulo de cuentas de cobro, atacados por la vía de servicio.

Mismo criterio que `web/test_invariantes.py`: saltarse la vista y atacar el
servicio, que es donde deben estar las reglas. El módulo aguantó casi todo; lo
único que cedió fue el mes de la cuenta, que admitía valores inexistentes
porque `CuentaEntrega` no llama a `full_clean()` y las `choices` de Django son
validación de formulario, no restricción de base.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

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


class ArrastreDeAprobadosTest(Base):
    """Una devolución ya no obliga a recargarlo todo.

    Antes la versión nueva nacía vacía y el contratista volvía a subir hasta lo
    que nadie había objetado. Ahora se arrastra lo aprobado y solo queda por
    recargar lo rechazado. Las revisiones **sí** se reinician: los tres vuelven
    a tener turno, empezando por el técnico.
    """

    def _devuelta(self):
        """Cuenta radicada, con un documento rechazado y devuelta por el técnico."""
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._asignar_todos(c)
        self.base._rechazar_un_doc(c)
        a_te = c.asignacionrevisor_set.get(
            rol=AsignacionRevisor.Rol.TECNICO,
            estado=AsignacionRevisor.Estado.ACTIVA,
        )
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.AJUSTES, "corrige"
        )
        c.refresh_from_db()
        return c

    def test_la_version_nueva_conserva_los_aprobados(self):
        c = self._devuelta()
        nueva = services.ultima_entrega(c)
        self.assertEqual(nueva.numero_version, 2)
        estados = list(nueva.documentoscuentacobro_set.values_list("estado", flat=True))
        self.assertEqual(estados, [DocumentosCuentaCobro.EstadoDocumento.APROBADO])

    def test_solo_se_pide_lo_rechazado(self):
        c = self._devuelta()
        faltantes = services.documentos_faltantes(c)
        self.assertEqual(len(faltantes), 1)
        # Y es justamente el tipo que se rechazó, no cualquiera.
        rechazado = DocumentosCuentaCobro.objects.filter(
            documento_entrega__cuenta_entrega=c,
            estado=DocumentosCuentaCobro.EstadoDocumento.RECHAZADO,
        ).first()
        self.assertEqual(faltantes[0].pk, rechazado.tipo_documento_id)

    def test_un_aprobado_arrastrado_no_se_puede_quitar(self):
        """Si no, borrarlo sería la puerta de atrás para recargarlo."""
        c = self._devuelta()
        doc = services.ultima_entrega(c).documentoscuentacobro_set.get()
        self.assertFalse(selectors.puede_eliminar_documento(self.base.contratista, doc))

    def test_las_revisiones_si_se_reinician(self):
        """Los tres vuelven a tener turno, empezando por el técnico.

        El arrastre es solo de documentos: la versión nueva nace sin ninguna
        revisión. Y nadie puede revisarla hasta que el contratista vuelva a
        entregar, aunque la mayoría de documentos ya vengan aprobados.
        """
        c = self._devuelta()
        nueva = services.ultima_entrega(c)
        self.assertEqual(nueva.revisioncuentacobro_set.count(), 0)
        for rol in RevisionCuentaCobro.Rol.values:
            self.assertFalse(services.rol_habilitado(nueva, rol), f"{rol} sin entregar")

        # El contratista recarga lo que falta y vuelve a entregar.
        for tipo in services.documentos_faltantes(c):
            services.adjuntar_documento(nueva, tipo, self.base._archivo_de_prueba())
        services.entregar(c, self.base.contratista)

        self.assertTrue(
            services.rol_habilitado(nueva, RevisionCuentaCobro.Rol.TECNICO)
        )
        for rol in (RevisionCuentaCobro.Rol.JURIDICO,
                    RevisionCuentaCobro.Rol.ADMINISTRATIVO):
            self.assertFalse(services.rol_habilitado(nueva, rol), rol)

    def test_el_archivo_compartido_sobrevive_al_borrado_de_una_version(self):
        """El riesgo que introduce el arrastre: dos filas, un solo objeto."""
        from django.core.files.storage import default_storage

        c = self._devuelta()
        arrastrado = services.ultima_entrega(c).documentoscuentacobro_set.get()
        clave = arrastrado.documento.name
        original = DocumentosCuentaCobro.objects.filter(
            documento=clave, documento_entrega__numero_version=1
        ).get()
        self.assertTrue(default_storage.exists(clave))

        with self.captureOnCommitCallbacks(execute=True):
            original.delete()      # se va la fila de la versión 1

        self.assertTrue(
            default_storage.exists(clave),
            "la versión 2 seguiría apuntando a un archivo que ya no está",
        )

    def test_al_irse_la_ultima_referencia_el_archivo_si_se_retira(self):
        from django.core.files.storage import default_storage

        c = self._devuelta()
        clave = services.ultima_entrega(c).documentoscuentacobro_set.get().documento.name
        with self.captureOnCommitCallbacks(execute=True):
            DocumentosCuentaCobro.objects.filter(documento=clave).delete()
        self.assertFalse(default_storage.exists(clave))


class HistorialDeVersionesTest(Base):
    """El contratista tiene que poder ver POR QUÉ le devolvieron la cuenta.

    El comentario general del revisor llegaba a la línea de tiempo, pero la
    causal documento a documento quedaba escrita en la versión anterior, que el
    detalle dejaba de mostrar en cuanto nacía la siguiente.
    """

    def _devuelta_con_causal(self, causal="Falta la firma del supervisor"):
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._asignar_todos(c)
        doc = services.ultima_entrega(c).documentoscuentacobro_set.first()
        services.revisar_documento(
            doc, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO,
            causal, self.base.rev_te,
        )
        a_te = c.asignacionrevisor_set.get(
            rol=AsignacionRevisor.Rol.TECNICO,
            estado=AsignacionRevisor.Estado.ACTIVA,
        )
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.AJUSTES, "revisa el soporte"
        )
        c.refresh_from_db()
        return c, causal

    def test_el_historial_trae_la_version_anterior_no_la_vigente(self):
        c, _ = self._devuelta_con_causal()
        historial = selectors.historial_de_versiones(c)
        self.assertEqual([v["entrega"].numero_version for v in historial], [1])

    def test_trae_la_causal_por_documento_y_la_revision(self):
        c, causal = self._devuelta_con_causal()
        v1 = selectors.historial_de_versiones(c)[0]
        self.assertEqual(len(v1["observados"]), 1)
        self.assertEqual(v1["observados"][0].comentario, causal)
        self.assertEqual(
            [r.resultado for r in v1["revisiones"]],
            [RevisionCuentaCobro.ResultadoRevision.AJUSTES],
        )

    def test_el_contratista_lo_ve_en_su_pantalla(self):
        c, causal = self._devuelta_con_causal()
        self.client.force_login(self.base.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        ).content.decode()
        self.assertIn("Versiones anteriores", cuerpo)
        self.assertIn(causal, cuerpo)
        self.assertIn("revisa el soporte", cuerpo)

    def test_es_solo_lectura(self):
        """Informativo: ni marcar, ni quitar, ni volver a cargar sobre lo viejo."""
        c, _ = self._devuelta_con_causal()
        v1 = selectors.historial_de_versiones(c)[0]
        doc_viejo = v1["documentos"][0]
        self.client.force_login(self.base.contratista)
        resp = self.client.post(
            reverse("cuentas_cobro:eliminar_archivo",
                    args=[c.pk, "documento", doc_viejo.pk])
        )
        self.assertEqual(resp.status_code, 403)

    def test_una_cuenta_sin_devoluciones_no_muestra_historial(self):
        c = self.base._cuenta_con_documentos()
        self.assertEqual(selectors.historial_de_versiones(c), [])

    def test_el_detalle_no_dispara_un_n1_por_version(self):
        """El historial recorre documentos y revisiones de todas las versiones."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        c, _ = self._devuelta_con_causal()
        self.client.force_login(self.base.contratista)
        url = reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        self.client.get(url)                       # calienta cachés de proceso
        with CaptureQueriesContext(connection) as q:
            self.client.get(url)
        self.assertLess(len(q), 45, f"{len(q)} consultas en el detalle")


class AdjuntosDeRevisionCuentasTest(Base):
    """El revisor puede acompañar su comentario con un soporte (opcional)."""

    def _en_revision(self):
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._asignar_todos(c)
        c.refresh_from_db()
        return c

    def _adjunto(self, cuenta, usuario):
        from django.contrib.contenttypes.models import ContentType
        from cuentas.models import AdjuntoDeRevision
        from cuentas_de_cobro.models import DocumentoEntrega

        return AdjuntoDeRevision.objects.create(
            content_type=ContentType.objects.get_for_model(DocumentoEntrega),
            object_id=services.ultima_entrega(cuenta).pk,
            usuario=usuario, nombre="Circular 005",
            archivo="revisiones/2026/10/circular.pdf",
        )

    def test_puede_adjuntar_el_revisor_asignado(self):
        c = self._en_revision()
        self.assertTrue(selectors.puede_adjuntar_en_revision(self.base.rev_te, c))

    def test_no_puede_el_contratista(self):
        c = self._en_revision()
        self.assertFalse(
            selectors.puede_adjuntar_en_revision(self.base.contratista, c)
        )
        self.client.force_login(self.base.contratista)
        resp = self.client.post(
            reverse("cuentas_cobro:subida_firmar", args=[c.pk, "adjunto_revision"]),
            {"nombre_documento": "X", "nombre": "a.pdf"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_el_contratista_si_lo_ve_y_lo_descarga(self):
        """Para eso existe: la observación va dirigida a él."""
        c = self._en_revision()
        adj = self._adjunto(c, self.base.rev_te)
        self.client.force_login(self.base.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        ).content.decode()
        self.assertIn("Circular 005", cuerpo)
        self.assertIn(
            reverse("cuentas_cobro:descargar", args=[c.pk, "adjunto", adj.pk]),
            cuerpo,
        )

    def test_no_se_adjunta_una_vez_decidio_el_supervisor(self):
        # `_aprobar_revisores` asigna por su cuenta, así que aquí se parte de
        # una cuenta radicada sin asignar.
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._aprobar_revisores(c)
        c.refresh_from_db()
        services.decidir_supervisor(
            c, self.base.supervisor, c.ResultadoRevision.APROBADA, "ok"
        )
        c.refresh_from_db()
        self.assertFalse(selectors.puede_adjuntar_en_revision(self.base.rev_te, c))

    def test_se_quita_mientras_ese_rol_no_haya_revisado(self):
        c = self._en_revision()
        adj = self._adjunto(c, self.base.rev_te)
        self.assertTrue(selectors.puede_quitar_adjunto(self.base.rev_te, adj, c))

        # El técnico registra su revisión: a partir de ahí, en firme.
        for doc in DocumentosCuentaCobro.objects.filter(
            documento_entrega__cuenta_entrega=c
        ):
            services.revisar_documento(
                doc, DocumentosCuentaCobro.EstadoDocumento.APROBADO, "ok",
                self.base.rev_te,
            )
        a_te = c.asignacionrevisor_set.get(
            rol=AsignacionRevisor.Rol.TECNICO,
            estado=AsignacionRevisor.Estado.ACTIVA,
        )
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok"
        )
        self.assertFalse(selectors.puede_quitar_adjunto(self.base.rev_te, adj, c))

    def test_un_adjunto_de_otra_cuenta_no_se_alcanza(self):
        """Cambiar el id en la URL no debe servir de nada."""
        c1 = self._en_revision()
        adj = self._adjunto(c1, self.base.rev_te)
        c2 = services.crear_cuenta(
            self.base.contratista, self.base.vigencia, 11, "noviembre"
        )
        self.client.force_login(self.base.rev_te)
        resp = self.client.get(
            reverse("cuentas_cobro:descargar", args=[c2.pk, "adjunto", adj.pk])
        )
        self.assertEqual(resp.status_code, 404)


class CargueDeSoportesSeCierraAlRevisarTest(Base):
    """Registrar la revisión cierra el cargue de soportes de ese rol.

    Reportado por el usuario: "después que revisé la cuenta me siguió
    apareciendo el formulario de cargue". Además era incoherente: ya no se podía
    QUITAR un soporte tras registrar la revisión, pero sí seguir añadiendo.
    """

    def _revisor_en_turno(self):
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._asignar_todos(c)
        c.refresh_from_db()
        a_te = c.asignacionrevisor_set.get(
            rol=AsignacionRevisor.Rol.TECNICO,
            estado=AsignacionRevisor.Estado.ACTIVA,
        )
        return c, a_te

    def test_antes_de_revisar_puede(self):
        c, _ = self._revisor_en_turno()
        self.assertTrue(selectors.puede_adjuntar_en_revision(self.base.rev_te, c))

    def test_despues_de_revisar_ya_no(self):
        c, a_te = self._revisor_en_turno()
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok"
        )
        c.refresh_from_db()
        self.assertFalse(selectors.puede_adjuntar_en_revision(self.base.rev_te, c))

    def test_el_formulario_desaparece_de_la_pantalla(self):
        c, a_te = self._revisor_en_turno()
        self.client.force_login(self.base.rev_te)
        url = reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        self.assertIn("Adjuntar soporte", self.client.get(url).content.decode())

        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok"
        )
        cuerpo = self.client.get(url).content.decode()
        self.assertNotIn("Adjuntar soporte", cuerpo)
        self.assertNotIn("Registrar revisión", cuerpo)

    def test_un_post_directo_tampoco_cuela(self):
        c, a_te = self._revisor_en_turno()
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok"
        )
        self.client.force_login(self.base.rev_te)
        resp = self.client.post(
            reverse("cuentas_cobro:subida_firmar", args=[c.pk, "adjunto_revision"]),
            {"nombre_documento": "Tardío", "nombre": "a.pdf"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_otro_revisor_que_aun_no_revisa_si_puede(self):
        """Se cierra por rol, no para todos."""
        c, a_te = self._revisor_en_turno()
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok"
        )
        c.refresh_from_db()
        self.assertTrue(selectors.puede_adjuntar_en_revision(self.base.rev_ju, c))

    def test_el_historial_llega_plegado(self):
        """Una cuenta con varias devoluciones acumula versiones."""
        c, a_te = self._revisor_en_turno()
        self.base._rechazar_un_doc(c)
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.AJUSTES, "corrige"
        )
        self.client.force_login(self.base.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        ).content.decode()
        self.assertIn("x-data=\"{ abierta: false }\"", cuerpo)
        self.assertNotIn("abierta: true", cuerpo)


class SoporteSobreviveALaDevolucionTest(Base):
    """Al devolver nace la versión siguiente en el acto, y eso movía dos cosas.

    Reportado por el usuario: "sigue apareciendo la opción de cargar soportes" y
    "en la versión revisada no veo el documento que se adjuntó". Misma causa: el
    detalle mira la versión vigente, que tras la devolución ya es otra.
    """

    def _con_soporte_y_devuelta(self):
        from django.contrib.contenttypes.models import ContentType
        from cuentas.models import AdjuntoDeRevision
        from cuentas_de_cobro.models import DocumentoEntrega

        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._asignar_todos(c)
        c.refresh_from_db()
        v1 = services.ultima_entrega(c)
        adj = AdjuntoDeRevision.objects.create(
            content_type=ContentType.objects.get_for_model(DocumentoEntrega),
            object_id=v1.pk, usuario=self.base.rev_te,
            nombre="Circular 005", archivo="revisiones/2026/10/c.pdf",
        )
        self.base._rechazar_un_doc(c)
        a_te = c.asignacionrevisor_set.get(
            rol=AsignacionRevisor.Rol.TECNICO,
            estado=AsignacionRevisor.Estado.ACTIVA,
        )
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.AJUSTES, "corrige"
        )
        c.refresh_from_db()
        return c, adj

    def test_el_cargue_no_se_reabre_al_devolver(self):
        """La pelota es del contratista: el revisor ya decidió."""
        c, _ = self._con_soporte_y_devuelta()
        self.assertFalse(selectors.puede_adjuntar_en_revision(self.base.rev_te, c))
        self.client.force_login(self.base.rev_te)
        resp = self.client.post(
            reverse("cuentas_cobro:subida_firmar", args=[c.pk, "adjunto_revision"]),
            {"nombre_documento": "Tardío", "nombre": "a.pdf"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_vuelve_a_abrirse_cuando_el_contratista_reentrega(self):
        c, _ = self._con_soporte_y_devuelta()
        for tipo in services.documentos_faltantes(c):
            services.adjuntar_documento(
                services.ultima_entrega(c), tipo, self.base._archivo_de_prueba()
            )
        services.entregar(c, self.base.contratista)
        self.assertTrue(selectors.puede_adjuntar_en_revision(self.base.rev_te, c))

    def test_el_soporte_sigue_visible_en_su_version(self):
        c, adj = self._con_soporte_y_devuelta()
        v1 = selectors.historial_de_versiones(c)[0]
        self.assertEqual([a.pk for a in v1["adjuntos"]], [adj.pk])

    def test_el_contratista_lo_ve_y_lo_descarga_desde_el_historial(self):
        c, adj = self._con_soporte_y_devuelta()
        self.client.force_login(self.base.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        ).content.decode()
        self.assertIn("Soportes adjuntados en esta versión", cuerpo)
        self.assertIn("Circular 005", cuerpo)
        self.assertIn(
            reverse("cuentas_cobro:descargar", args=[c.pk, "adjunto", adj.pk]),
            cuerpo,
        )


class IconoDeQuitarSoloDondeSePuedeTest(Base):
    """La interfaz no ofrece lo que el servidor va a rechazar.

    Reportado por el usuario: al contratista le aparecía el icono de eliminar
    sobre un documento ya aprobado y, al pulsarlo, un 403 que no le dice nada.
    El permiso estaba bien; lo que fallaba era ofrecerlo.
    """

    def _devuelta(self):
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._asignar_todos(c)
        self.base._rechazar_un_doc(c)
        a_te = c.asignacionrevisor_set.get(
            rol=AsignacionRevisor.Rol.TECNICO,
            estado=AsignacionRevisor.Estado.ACTIVA,
        )
        services.registrar_revision(
            a_te, RevisionCuentaCobro.ResultadoRevision.AJUSTES, "corrige"
        )
        c.refresh_from_db()
        return c

    def test_el_arrastrado_no_ofrece_el_icono(self):
        c = self._devuelta()
        arrastrado = services.ultima_entrega(c).documentoscuentacobro_set.get()
        self.client.force_login(self.base.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        ).content.decode()
        self.assertNotIn(
            reverse("cuentas_cobro:eliminar_archivo",
                    args=[c.pk, "documento", arrastrado.pk]),
            cuerpo,
        )

    def test_lo_que_el_contratista_acaba_de_cargar_si_lo_ofrece(self):
        """No se cierra de más: lo recién subido sigue siendo corregible."""
        c = self._devuelta()
        entrega = services.ultima_entrega(c)
        tipo = services.documentos_faltantes(c)[0]
        nuevo = services.adjuntar_documento(
            entrega, tipo, self.base._archivo_de_prueba()
        )
        self.client.force_login(self.base.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        ).content.decode()
        self.assertIn(
            reverse("cuentas_cobro:eliminar_archivo",
                    args=[c.pk, "documento", nuevo.pk]),
            cuerpo,
        )

    def test_la_interfaz_y_el_permiso_dicen_lo_mismo(self):
        """Que no se vuelvan a separar: lo ofrecido es exactamente lo permitido."""
        c = self._devuelta()
        entrega = services.ultima_entrega(c)
        services.adjuntar_documento(
            entrega, services.documentos_faltantes(c)[0],
            self.base._archivo_de_prueba(),
        )
        self.client.force_login(self.base.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[c.pk])
        ).content.decode()
        for doc in entrega.documentoscuentacobro_set.all():
            url = reverse("cuentas_cobro:eliminar_archivo",
                          args=[c.pk, "documento", doc.pk])
            permitido = selectors.puede_eliminar_documento(self.base.contratista, doc)
            self.assertEqual(url in cuerpo, permitido, doc.tipo_documento.nombre)


class RechazoTardioDeUnArrastradoTest(Base):
    """Un revisor posterior puede objetar lo que el anterior aprobó.

    El arrastre no congela nada: cada versión tiene su propia fila por
    documento, así que marcar un rechazo toca la versión en curso y deja
    intactas las anteriores. El historial no se reescribe.
    """

    def _rol(self, cuenta, rol):
        return cuenta.asignacionrevisor_set.get(
            rol=rol, estado=AsignacionRevisor.Estado.ACTIVA
        )

    def test_el_juridico_objeta_lo_que_el_tecnico_aprobo(self):
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._asignar_todos(c)
        c.refresh_from_db()
        v1 = services.ultima_entrega(c)

        # El técnico aprueba toda la versión.
        for d in v1.documentoscuentacobro_set.all():
            services.revisar_documento(
                d, DocumentosCuentaCobro.EstadoDocumento.APROBADO, "ok",
                self.base.rev_te,
            )
        services.registrar_revision(
            self._rol(c, AsignacionRevisor.Rol.TECNICO),
            RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok",
        )

        # El jurídico objeta uno de ellos y devuelve.
        objetado = v1.documentoscuentacobro_set.order_by("tipo_documento__nombre").first()
        tipo_objetado = objetado.tipo_documento
        services.revisar_documento(
            objetado, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO,
            "le falta la firma", self.base.rev_ju,
        )
        services.registrar_revision(
            self._rol(c, AsignacionRevisor.Rol.JURIDICO),
            RevisionCuentaCobro.ResultadoRevision.AJUSTES, "corrige",
        )
        c.refresh_from_db()
        v2 = services.ultima_entrega(c)

        # 1. El objetado NO se arrastra: hay que volver a cargarlo.
        self.assertFalse(
            v2.documentoscuentacobro_set.filter(tipo_documento=tipo_objetado).exists()
        )
        self.assertEqual(
            [t.pk for t in services.documentos_faltantes(c)], [tipo_objetado.pk]
        )

        # 2. Los demás sí, con su estado.
        otros = v2.documentoscuentacobro_set.all()
        self.assertTrue(otros.exists())
        self.assertTrue(
            all(d.estado == DocumentosCuentaCobro.EstadoDocumento.APROBADO
                for d in otros)
        )

        # 3. La versión anterior conserva lo que se decidió en ella: el rechazo
        #    quedó en la v1, que es donde se marcó.
        objetado.refresh_from_db()
        self.assertEqual(
            objetado.estado, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO
        )
        self.assertEqual(objetado.documento_entrega_id, v1.pk)
        self.assertEqual(objetado.comentario, "le falta la firma")

    def test_el_historial_muestra_la_aprobacion_y_el_rechazo_juntos(self):
        """Lo que verá el contratista, y por qué no es contradictorio.

        En la misma versión conviven la revisión del técnico (Aprobado, tomada
        sobre el paquete tal como estaba) y el documento en Rechazado, porque
        el jurídico lo objetó después. Las revisiones son históricas; el estado
        del documento es el último.
        """
        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._asignar_todos(c)
        c.refresh_from_db()
        v1 = services.ultima_entrega(c)
        for d in v1.documentoscuentacobro_set.all():
            services.revisar_documento(
                d, DocumentosCuentaCobro.EstadoDocumento.APROBADO, "ok",
                self.base.rev_te,
            )
        services.registrar_revision(
            self._rol(c, AsignacionRevisor.Rol.TECNICO),
            RevisionCuentaCobro.ResultadoRevision.APROBADA, "ok",
        )
        objetado = v1.documentoscuentacobro_set.order_by("tipo_documento__nombre").first()
        services.revisar_documento(
            objetado, DocumentosCuentaCobro.EstadoDocumento.RECHAZADO,
            "le falta la firma", self.base.rev_ju,
        )
        services.registrar_revision(
            self._rol(c, AsignacionRevisor.Rol.JURIDICO),
            RevisionCuentaCobro.ResultadoRevision.AJUSTES, "corrige",
        )
        c.refresh_from_db()

        fila = selectors.historial_de_versiones(c)[0]
        self.assertEqual(fila["entrega"].pk, v1.pk)
        resultados = {r.rol: r.resultado for r in fila["revisiones"]}
        self.assertEqual(
            resultados[RevisionCuentaCobro.Rol.TECNICO],
            RevisionCuentaCobro.ResultadoRevision.APROBADA,
        )
        self.assertEqual(
            resultados[RevisionCuentaCobro.Rol.JURIDICO],
            RevisionCuentaCobro.ResultadoRevision.AJUSTES,
        )
        self.assertEqual([d.pk for d in fila["observados"]], [objetado.pk])


class MensajeAlResponderUnTramiteTest(Base):
    """Responder el último trámite cierra la cuenta, y hay que decirlo.

    Reportado por el usuario: hizo SIIFWEB y luego SECOP, y entendió que "no
    terminó la cuenta". Sí la había terminado: el evento de cierre y
    `fecha_cierre` quedaron escritos en el mismo segundo. Lo que faltaba era
    que la aplicación se lo dijera; el aviso decía "Documento cargado", igual
    que al subir cualquier otra cosa.
    """

    def _hasta_los_tramites(self):
        from cuentas_de_cobro.views import _mensaje_de_subida

        c = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._aprobar_revisores(c)
        c.refresh_from_db()
        services.decidir_supervisor(
            c, self.base.supervisor, c.ResultadoRevision.APROBADA, "ok"
        )
        c.refresh_from_db()
        for tipo in services.tipos_obligatorios(c):
            services.cargar_documento_cierre(
                c, tipo, self.base._archivo_de_prueba(), self.base.radicador
            )
        return c, _mensaje_de_subida

    def test_tras_el_primer_tramite_dice_que_falta(self):
        c, mensaje = self._hasta_los_tramites()
        services.responder_tramite(
            c, "SF", self.base.rev_ad, True,
            self.base._archivo_de_prueba("siif.pdf"), "cargado",
        )
        texto = mensaje("tramite", c)
        self.assertIn("Falta", texto)
        self.assertIn("SECOP", texto)
        self.assertNotIn("CERRADA", texto)

    def test_tras_el_ultimo_dice_que_la_cuenta_quedo_cerrada(self):
        c, mensaje = self._hasta_los_tramites()
        services.responder_tramite(
            c, "SF", self.base.rev_ad, True,
            self.base._archivo_de_prueba("siif.pdf"), "cargado",
        )
        services.responder_tramite(
            c, "SC", self.base.secop, True,
            self.base._archivo_de_prueba("secop.pdf"), "publicado",
        )
        c.refresh_from_db()
        self.assertIsNotNone(c.fecha_cierre)
        self.assertIn("CERRADA", mensaje("tramite", c))

    def test_cada_destino_dice_lo_suyo(self):
        c, mensaje = self._hasta_los_tramites()
        self.assertIn("cierre", mensaje("cierre", c))
        self.assertIn("Soporte", mensaje("adjunto_revision", c))
        self.assertIn("Documento cargado", mensaje("entrega", c))


class DecisionDelSupervisorSondeo(Base):
    """La decisión del supervisor se toma una sola vez.

    `revisar_supervisor` (modelo, definitivo) sobreescribe el estado y la fecha
    de aprobación sin mirar si ya había decisión. Sin guarda en el servicio, un
    doble clic movía la fecha y duplicaba el asiento de la bitácora, y un POST
    posterior podía dar por rechazada una cuenta con el cierre ya cargado.
    """

    def _aprobada_por_revisores(self):
        c = self.base._radicar(self._entregada())
        self.base._aprobar_revisores(c)
        c.refresh_from_db()
        return c

    def test_no_se_decide_dos_veces(self):
        c = self._aprobada_por_revisores()
        services.decidir_supervisor(
            c, self.base.supervisor, CuentaEntrega.ResultadoRevision.APROBADA, "ok"
        )
        c.refresh_from_db()
        fecha = c.fecha_aprobacion_supervisor
        with self.assertRaises(ValidationError):
            services.decidir_supervisor(
                c, self.base.supervisor,
                CuentaEntrega.ResultadoRevision.APROBADA, "otra vez",
            )
        c.refresh_from_db()
        self.assertEqual(c.fecha_aprobacion_supervisor, fecha)
        self.assertEqual(
            c.eventos.filter(evento=services.Eventos.SUP_APROBADO).count(), 1
        )

    def test_no_se_rechaza_lo_que_ya_se_aprobo(self):
        """Lo grave: el cierre ya está cargado y el expediente firmado."""
        c = self._aprobada_por_revisores()
        services.decidir_supervisor(
            c, self.base.supervisor, CuentaEntrega.ResultadoRevision.APROBADA, "ok"
        )
        c.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.decidir_supervisor(
                c, self.base.supervisor,
                CuentaEntrega.ResultadoRevision.RECHAZADA, "me arrepentí",
            )
        c.refresh_from_db()
        self.assertEqual(
            c.estado_supervisor, CuentaEntrega.ResultadoRevision.APROBADA
        )

    def test_un_rechazo_del_supervisor_tampoco_se_revierte(self):
        c = self._aprobada_por_revisores()
        services.decidir_supervisor(
            c, self.base.supervisor, CuentaEntrega.ResultadoRevision.RECHAZADA, "no"
        )
        c.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.decidir_supervisor(
                c, self.base.supervisor,
                CuentaEntrega.ResultadoRevision.APROBADA, "ahora sí",
            )
        c.refresh_from_db()
        self.assertEqual(
            c.estado_supervisor, CuentaEntrega.ResultadoRevision.RECHAZADA
        )
