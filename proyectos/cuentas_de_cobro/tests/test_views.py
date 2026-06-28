"""Smoke tests de vistas: render por rol y control de acceso básico."""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from cuentas_de_cobro import services
from cuentas_de_cobro.models import (
    CuentaEntrega,
    RequisitoDocumental,
    TipoDocumentoCargue,
    Vigencia,
)


class VistasTest(TestCase):
    def setUp(self):
        self.contratista = User.objects.create_user("contra", password="x")
        self.supervisor = User.objects.create_user("super", password="x")
        self.revisor = User.objects.create_user("rev", password="x")
        self.ajeno = User.objects.create_user("ajeno", password="x")
        self.contratista.groups.add(Group.objects.get(name="Contratista"))
        self.supervisor.groups.add(Group.objects.get(name="Supervisor"))
        self.revisor.groups.add(Group.objects.get(name="Revisor"))

        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.tipo = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        RequisitoDocumental.objects.create(vigencia=self.vigencia, tipo_documento=self.tipo)
        self.cuenta = services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")

    def test_bandeja_render_por_rol(self):
        for u in (self.contratista, self.supervisor, self.revisor):
            self.client.force_login(u)
            resp = self.client.get(reverse("cuentas_cobro:bandeja"))
            self.assertEqual(resp.status_code, 200)

    def test_detalle_render_supervisor_y_contratista(self):
        # El supervisor ve la cuenta una vez entregada (intervención/acción).
        from django.core.files.uploadedfile import SimpleUploadedFile

        entrega = services.ultima_entrega(self.cuenta)
        services.adjuntar_documento(
            entrega, self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"))
        services.entregar(self.cuenta, self.contratista)
        url = reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        for u in (self.contratista, self.supervisor):
            self.client.force_login(u)
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_usuario_sin_rol_no_entra(self):
        self.client.force_login(self.ajeno)
        resp = self.client.get(reverse("cuentas_cobro:bandeja"))
        self.assertEqual(resp.status_code, 403)

    def test_contratista_no_ve_cuenta_ajena(self):
        otro = User.objects.create_user("otro", password="x")
        otro.groups.add(Group.objects.get(name="Contratista"))
        self.client.force_login(otro)
        url = reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_supervisor_no_puede_cargar_documentos(self):
        from cuentas_de_cobro import selectors

        from django.core.files.uploadedfile import SimpleUploadedFile

        self.assertFalse(selectors.puede_cargar_documentos(self.supervisor, self.cuenta))
        self.assertFalse(selectors.puede_cargar_documentos(self.revisor, self.cuenta))
        # Entregar la cuenta para que el supervisor la vea; aun así no puede entregar.
        entrega = services.ultima_entrega(self.cuenta)
        services.adjuntar_documento(
            entrega, self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"))
        services.entregar(self.cuenta, self.contratista)
        self.client.force_login(self.supervisor)
        url = reverse("cuentas_cobro:entregar", args=[self.cuenta.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_contratista_carga_documento(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.contratista)
        url = reverse("cuentas_cobro:documento_cargar", args=[self.cuenta.pk])
        resp = self.client.post(url, {
            "tipo_documento": self.tipo.pk,
            "documento": SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        })
        self.assertEqual(resp.status_code, 302)
        entrega = services.ultima_entrega(self.cuenta)
        self.assertEqual(entrega.documentoscuentacobro_set.count(), 1)

    def test_form_de_carga_se_oculta_si_no_faltan_documentos(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        entrega = services.ultima_entrega(self.cuenta)
        services.adjuntar_documento(
            entrega, self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        )
        self.assertEqual(services.documentos_faltantes(self.cuenta), [])
        self.client.force_login(self.contratista)
        resp = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        )
        cuerpo = resp.content.decode()
        accion = reverse("cuentas_cobro:documento_cargar", args=[self.cuenta.pk])
        self.assertNotIn(accion, cuerpo)
        self.assertIn("Cargaste todos los documentos obligatorios", cuerpo)
