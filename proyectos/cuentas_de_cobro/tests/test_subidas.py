"""Subida directa al bucket: permisos, validación previa y registro sin re-subir.

Lo que toca de verdad el bucket (firmar la URL y comprobar el objeto con
`head_object`) no se puede probar aquí: el almacenamiento de pruebas es en
memoria. Lo que sí se prueba es todo lo que se decide ANTES de llegar al bucket
(quién puede subir, qué tipo y qué extensión se admiten) y el registro posterior
a partir de una clave, que es el mecanismo que evita volver a transferir el
archivo.
"""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from cuentas.subidas import clave_de_objeto
from cuentas_de_cobro import selectors, services
from cuentas_de_cobro.models import (
    DocumentosCuentaCobro,
    RequisitoDocumental,
    TipoDocumentoCargue,
    Vigencia,
)


class ClaveDeObjetoTest(TestCase):
    def test_normaliza_el_nombre_y_reparte_por_fecha(self):
        clave = clave_de_objeto("cuentas_cobro/%Y/%m/", "Documentación año.pdf")
        self.assertRegex(clave, r"^cuentas_cobro/\d{4}/\d{2}/[0-9a-f]{8}_Documentacion_ano\.pdf$")

    def test_dos_subidas_del_mismo_nombre_no_chocan(self):
        a = clave_de_objeto("cierres/%Y/%m/", "planilla.pdf")
        b = clave_de_objeto("cierres/%Y/%m/", "planilla.pdf")
        self.assertNotEqual(a, b)


class SubidaDirectaVistasTest(TestCase):
    def setUp(self):
        self.contratista = User.objects.create_user("c1", password="x")
        self.contratista.groups.add(Group.objects.get(name="Contratista"))
        self.ajeno = User.objects.create_user("c2", password="x")
        self.ajeno.groups.add(Group.objects.get(name="Contratista"))
        self.supervisor = User.objects.create_user("s1", password="x")
        self.supervisor.groups.add(Group.objects.get(name="Supervisor"))

        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.tipo = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        RequisitoDocumental.objects.create(
            vigencia=self.vigencia, tipo_documento=self.tipo, obligatorio=True
        )
        self.ajena = TipoDocumentoCargue.objects.create(nombre="De otra vigencia")
        self.cuenta = services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")

    def _firmar(self, **datos):
        url = reverse("cuentas_cobro:subida_firmar", args=[self.cuenta.pk, "entrega"])
        return self.client.post(url, datos)

    def test_el_supervisor_no_ve_una_cuenta_que_no_se_ha_entregado(self):
        """Ni llega a evaluarse el permiso: la cuenta no está en su alcance."""
        self.client.force_login(self.supervisor)
        resp = self._firmar(tipo_documento=self.tipo.pk, nombre="a.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_el_supervisor_no_puede_subir_documentos_de_la_entrega(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        services.adjuntar_documento(
            services.ultima_entrega(self.cuenta), self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        )
        services.entregar(self.cuenta, self.contratista)  # ahora sí la ve
        self.client.force_login(self.supervisor)
        resp = self._firmar(tipo_documento=self.tipo.pk, nombre="a.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_el_contratista_tampoco_sube_tras_entregar(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        services.adjuntar_documento(
            services.ultima_entrega(self.cuenta), self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        )
        services.entregar(self.cuenta, self.contratista)
        self.client.force_login(self.contratista)
        resp = self._firmar(tipo_documento=self.tipo.pk, nombre="a.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_un_contratista_ajeno_no_ve_la_cuenta(self):
        self.client.force_login(self.ajeno)
        resp = self._firmar(tipo_documento=self.tipo.pk, nombre="a.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_rechaza_una_extension_no_admitida(self):
        self.client.force_login(self.contratista)
        resp = self._firmar(tipo_documento=self.tipo.pk, nombre="virus.exe")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no es un tipo de archivo admitido", resp.json()["error"])

    def test_rechaza_un_tipo_que_no_es_de_la_vigencia(self):
        self.client.force_login(self.contratista)
        resp = self._firmar(tipo_documento=self.ajena.pk, nombre="a.pdf")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no aplica a esta vigencia", resp.json()["error"])

    def test_rechaza_un_destino_inventado(self):
        self.client.force_login(self.contratista)
        url = reverse("cuentas_cobro:subida_firmar", args=[self.cuenta.pk, "otro"])
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_el_detalle_pinta_el_componente_con_sus_urls(self):
        """Si el `include` quedara mal, Django no se queja: lo pintaría vacío."""
        self.client.force_login(self.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        self.assertIn(
            reverse("cuentas_cobro:subida_firmar", args=[self.cuenta.pk, "entrega"]),
            cuerpo,
        )
        self.assertIn(
            reverse("cuentas_cobro:subida_confirmar", args=[self.cuenta.pk, "entrega"]),
            cuerpo,
        )
        self.assertIn("subidaDirecta(", cuerpo)
        self.assertIn("progress", cuerpo)
        # Y ofrece el tipo que falta por cargar.
        self.assertIn(self.tipo.nombre, cuerpo)
        # El paso de confirmación es igual en todos los cargues, también en los
        # que sí se pueden deshacer: la pausa es la misma en todo el sistema.
        self.assertIn("Confirmar envío", cuerpo)
        self.assertIn("Volver a elegir", cuerpo)
        self.assertIn("Revisa antes de enviar", cuerpo)

    def test_no_confirma_con_un_token_manipulado(self):
        self.client.force_login(self.contratista)
        url = reverse(
            "cuentas_cobro:subida_confirmar", args=[self.cuenta.pk, "entrega"]
        )
        resp = self.client.post(url, {"token": "esto.no.esta.firmado"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no es válida", resp.json()["error"])
        self.assertEqual(
            DocumentosCuentaCobro.objects.filter(
                documento_entrega__cuenta_entrega=self.cuenta
            ).count(),
            0,
        )


class SubidaDeTramiteFinalTest(TestCase):
    """La evidencia del trámite final usa el mismo mecanismo, con su comentario."""

    def setUp(self):
        from .test_services import FlujoBaseTest  # andamiaje del flujo completo

        self.base = FlujoBaseTest("run")
        self.base.setUp()
        cuenta = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        services.decidir_supervisor(
            cuenta, self.base.supervisor,
            cuenta.ResultadoRevision.APROBADA, "ok",
        )
        cuenta.refresh_from_db()
        for tipo in services.tipos_obligatorios(cuenta):
            services.cargar_documento_cierre(
                cuenta, tipo, self.base._archivo_de_prueba(), self.base.radicador
            )
        self.cuenta = cuenta

    def _firmar(self, user, **datos):
        self.client.force_login(user)
        url = reverse("cuentas_cobro:subida_firmar", args=[self.cuenta.pk, "tramite"])
        return self.client.post(url, datos)

    def test_el_revisor_administrativo_puede_firmar_el_siifweb(self):
        resp = self._firmar(
            self.base.rev_ad, tipo_tramite="SF", nombre="evidencia.pdf"
        )
        # Sin bucket no se puede firmar de verdad, pero el permiso y el tipo ya
        # se validaron: el error que queda es el del almacenamiento.
        self.assertEqual(resp.status_code, 400)
        self.assertIn("almacenamiento", resp.json()["error"])

    def test_el_secop_no_puede_firmar_el_siifweb(self):
        resp = self._firmar(self.base.secop, tipo_tramite="SF", nombre="e.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_el_tramite_avisa_que_la_respuesta_no_se_deshace(self):
        """El aviso se lee al confirmar, que es el momento en que importa.

        Responder un trámite no se puede deshacer, y el de SECOP II además cierra
        la cuenta: el usuario tiene que saberlo antes de pulsar, no después.
        """
        self.client.force_login(self.base.rev_ad)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        self.assertIn("Confirmar envío", cuerpo)
        self.assertIn("Volver a elegir", cuerpo)
        self.assertIn("solo podrás cambiar el soporte hasta que se responda", cuerpo)

    def test_el_secop_avisa_que_cierra_la_cuenta(self):
        services.responder_tramite(
            self.cuenta, "SF", self.base.rev_ad, True,
            self.base._archivo_de_prueba("siif.pdf"), "cargado",
        )
        self.client.force_login(self.base.secop)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        self.assertIn("la cuenta se cierra", cuerpo)
        self.assertIn("ya no se podrá cambiar", cuerpo)

    def test_rechaza_un_tipo_de_tramite_inventado(self):
        resp = self._firmar(self.base.rev_ad, tipo_tramite="XX", nombre="e.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_el_detalle_pinta_el_componente_con_el_tipo_de_tramite(self):
        self.client.force_login(self.base.rev_ad)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        self.assertIn(
            reverse("cuentas_cobro:subida_firmar", args=[self.cuenta.pk, "tramite"]),
            cuerpo,
        )
        self.assertIn('name="tipo_tramite" value="SF"', cuerpo)
        self.assertIn('name="comentario"', cuerpo)


class EvidenciaNoSeReemplazaTest(TestCase):
    """La evidencia de un trámite respondido NO se cambia, por ninguna vía.

    Existió un tiempo, para corregir una captura equivocada. Se retiró porque el
    cargue pide confirmación antes de registrar nada: ahí es donde se evita el
    archivo equivocado, y un soporte que se puede cambiar después debilita el
    valor probatorio del expediente.
    """

    def setUp(self):
        from .test_services import FlujoBaseTest

        self.base = FlujoBaseTest("run")
        self.base.setUp()
        cuenta = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        services.decidir_supervisor(
            cuenta, self.base.supervisor, cuenta.ResultadoRevision.APROBADA, "ok"
        )
        cuenta.refresh_from_db()
        for tipo in services.tipos_obligatorios(cuenta):
            services.cargar_documento_cierre(
                cuenta, tipo, self.base._archivo_de_prueba(), self.base.radicador
            )
        services.responder_tramite(
            cuenta, "SF", self.base.rev_ad, True,
            self.base._archivo_de_prueba("siif.pdf"), "cargado",
        )
        cuenta.refresh_from_db()
        self.cuenta = cuenta

    def test_el_detalle_no_lo_ofrece(self):
        self.client.force_login(self.base.rev_ad)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        self.assertIn("Ver evidencia", cuerpo)        # consultarla, sí
        self.assertNotIn("Reemplazar evidencia", cuerpo)

    def test_el_destino_de_subida_ya_no_existe(self):
        """Ni por un POST directo: 'evidencia' dejó de ser un destino válido."""
        self.client.force_login(self.base.rev_ad)
        for ruta in ("subida_firmar", "subida_confirmar"):
            resp = self.client.post(
                reverse(f"cuentas_cobro:{ruta}", args=[self.cuenta.pk, "evidencia"]),
                {"tipo_tramite": "SF", "nombre": "otra.pdf"},
            )
            self.assertEqual(resp.status_code, 403, ruta)

    def test_el_soporte_registrado_no_cambia(self):
        tramite = services.tramite_de(self.cuenta, "SF")
        self.assertTrue(tramite.realizado)
        self.assertTrue(tramite.evidencia.name)
        self.assertFalse(hasattr(services, "reemplazar_evidencia"))
        self.assertFalse(hasattr(selectors, "puede_reemplazar_evidencia"))



class CierreNoSeQuitaTest(TestCase):
    """Un documento de cierre NO se retira, ni por la interfaz ni por un POST.

    Se permitió un tiempo y resultó contraproducente: completar el cierre habilita
    el cargue en SIIFWEB, y retirar un documento después lo deshabilitaba otra vez,
    así que la cuenta parecía trabada sin que se viera el porqué. Y como
    `DocumentoCierre` es único por (cuenta, tipo), tras retirarlo tampoco se podía
    volver a cargar ese tipo.
    """

    def setUp(self):
        from .test_services import FlujoBaseTest

        self.base = FlujoBaseTest("run")
        self.base.setUp()
        cuenta = self.base._radicar(self.base._cuenta_con_documentos())
        self.base._aprobar_revisores(cuenta)
        cuenta.refresh_from_db()
        services.decidir_supervisor(
            cuenta, self.base.supervisor, cuenta.ResultadoRevision.APROBADA, "ok"
        )
        cuenta.refresh_from_db()
        for tipo in services.tipos_obligatorios(cuenta):
            services.cargar_documento_cierre(
                cuenta, tipo, self.base._archivo_de_prueba(), self.base.radicador
            )
        self.cuenta = cuenta
        self.cierre = cuenta.documentocierre_set.first()

    def test_el_detalle_no_ofrece_quitarlo(self):
        self.client.force_login(self.base.radicador)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        self.assertIn("(firmado)", cuerpo)  # el documento sí se lista
        self.assertNotIn(
            reverse(
                "cuentas_cobro:eliminar_archivo",
                args=[self.cuenta.pk, "cierre", self.cierre.pk],
            ),
            cuerpo,
        )

    def test_un_post_directo_tampoco_lo_quita(self):
        self.client.force_login(self.base.radicador)
        resp = self.client.post(
            reverse(
                "cuentas_cobro:eliminar_archivo",
                args=[self.cuenta.pk, "cierre", self.cierre.pk],
            )
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.cuenta.documentocierre_set.count(), 2)

    def test_el_siifweb_sigue_habilitado(self):
        """Lo que se estaba rompiendo: el cierre completo habilita el trámite."""
        self.assertFalse(services.documentos_cierre_faltantes(self.cuenta))
        self.assertTrue(services.tramite_habilitado(self.cuenta, "SF"))
        self.assertTrue(
            selectors.puede_responder_tramite(self.base.rev_ad, self.cuenta, "SF")
        )


class EliminarDocumentoTest(TestCase):
    """Quitar un documento cargado por equivocación, mientras el flujo lo permita."""

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.contratista = User.objects.create_user("c1", password="x")
        self.contratista.groups.add(Group.objects.get(name="Contratista"))
        self.supervisor = User.objects.create_user("s1", password="x")
        self.supervisor.groups.add(Group.objects.get(name="Supervisor"))
        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.tipo = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        RequisitoDocumental.objects.create(
            vigencia=self.vigencia, tipo_documento=self.tipo, obligatorio=True
        )
        self.cuenta = services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")
        self.doc = services.adjuntar_documento(
            services.ultima_entrega(self.cuenta), self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        )

    def _url(self, pk=None):
        return reverse(
            "cuentas_cobro:eliminar_archivo",
            args=[self.cuenta.pk, "documento", pk or self.doc.pk],
        )

    def test_el_dueno_lo_quita_antes_de_entregar(self):
        self.client.force_login(self.contratista)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            DocumentosCuentaCobro.objects.filter(pk=self.doc.pk).count(), 0
        )
        # Y queda constancia en la bitácora.
        self.assertTrue(
            self.cuenta.eventos.filter(evento=services.Eventos.DOC_ELIMINADO).exists()
        )

    def test_tras_entregar_ya_no_se_puede(self):
        services.entregar(self.cuenta, self.contratista)
        self.client.force_login(self.contratista)
        self.assertEqual(self.client.post(self._url()).status_code, 403)
        self.assertEqual(
            DocumentosCuentaCobro.objects.filter(pk=self.doc.pk).count(), 1
        )

    def test_el_supervisor_no_quita_documentos_del_contratista(self):
        services.entregar(self.cuenta, self.contratista)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.post(self._url()).status_code, 403)

    def test_no_se_quita_con_get(self):
        """Una acción destructiva no se dispara abriendo un enlace."""
        self.client.force_login(self.contratista)
        self.assertEqual(self.client.get(self._url()).status_code, 405)
        self.assertEqual(
            DocumentosCuentaCobro.objects.filter(pk=self.doc.pk).count(), 1
        )

    def test_el_detalle_ofrece_quitarlo(self):
        self.client.force_login(self.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        self.assertIn(self._url(), cuerpo)
        self.assertIn("Eliminar", cuerpo)


class DescargaDeArchivosTest(TestCase):
    """La descarga pasa por el servidor: comprueba el acceso y pone el nombre."""

    def setUp(self):
        self.contratista = User.objects.create_user("c1", password="x")
        self.contratista.groups.add(Group.objects.get(name="Contratista"))
        self.ajeno = User.objects.create_user("c2", password="x")
        self.ajeno.groups.add(Group.objects.get(name="Contratista"))
        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.tipo = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        RequisitoDocumental.objects.create(
            vigencia=self.vigencia, tipo_documento=self.tipo, obligatorio=True
        )
        self.cuenta = services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.doc = services.adjuntar_documento(
            services.ultima_entrega(self.cuenta), self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        )

    def _url(self, tipo="documento", pk=None):
        return reverse(
            "cuentas_cobro:descargar",
            args=[self.cuenta.pk, tipo, pk or self.doc.pk],
        )

    def test_un_contratista_ajeno_no_descarga(self):
        self.client.force_login(self.ajeno)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_el_dueno_llega_al_almacenamiento(self):
        """Sin bucket no se puede firmar, pero el acceso ya se comprobó: lo que
        queda es el aviso de almacenamiento, no un 403."""
        self.client.force_login(self.contratista)
        resp = self.client.get(self._url(), follow=True)
        self.assertEqual(resp.status_code, 200)
        avisos = " ".join(m.message for m in resp.context["messages"])
        self.assertIn("almacenamiento", avisos)

    def test_un_tipo_desconocido_se_rechaza(self):
        self.client.force_login(self.contratista)
        self.assertEqual(self.client.get(self._url(tipo="otro")).status_code, 403)

    def test_un_documento_de_otra_cuenta_no_se_descarga(self):
        otra = services.crear_cuenta(self.contratista, self.vigencia, 7, "julio")
        url = reverse(
            "cuentas_cobro:descargar", args=[otra.pk, "documento", self.doc.pk]
        )
        self.client.force_login(self.contratista)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_el_detalle_ofrece_abrir_y_descargar(self):
        self.client.force_login(self.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        self.assertIn(self._url(), cuerpo)
        self.assertIn("Descargar", cuerpo)


class RegistroSinResubirTest(TestCase):
    """El archivo ya está en el bucket: solo se guarda la ruta."""

    def setUp(self):
        self.contratista = User.objects.create_user("c1", password="x")
        self.contratista.groups.add(Group.objects.get(name="Contratista"))
        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.tipo = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        RequisitoDocumental.objects.create(
            vigencia=self.vigencia, tipo_documento=self.tipo, obligatorio=True
        )
        self.cuenta = services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")

    def test_adjuntar_documento_con_clave_guarda_solo_la_ruta(self):
        clave = "cuentas_cobro/2026/10/abcd1234_planilla.pdf"
        doc = services.adjuntar_documento(
            services.ultima_entrega(self.cuenta), self.tipo, clave=clave
        )
        doc.refresh_from_db()
        self.assertEqual(doc.documento.name, clave)
        self.assertEqual(doc.estado, DocumentosCuentaCobro.EstadoDocumento.PENDIENTE)

    def test_sigue_respetando_las_guardas_de_la_entrega(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile

        entrega = services.ultima_entrega(self.cuenta)
        services.adjuntar_documento(
            entrega, self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        )
        services.entregar(self.cuenta, self.contratista)
        # Paquete ya entregado: tampoco se puede colar por la vía directa.
        with self.assertRaises(ValidationError):
            services.adjuntar_documento(
                entrega, self.tipo, clave="cuentas_cobro/2026/10/x_otro.pdf"
            )

    def test_sin_archivo_ni_clave_falla(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            services.adjuntar_documento(
                services.ultima_entrega(self.cuenta), self.tipo
            )
