"""Parámetros de la plataforma que administra el usuario, no el código.

Vive en `cuentas` y no en `contenido` ni en `cuentas_de_cobro` porque esos dos
conjuntos de modelos son definitivos y no se tocan, y porque el validador que lo
usa (`cuentas/validadores.py`) ya está aquí y lo comparten los dos dominios.
"""
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models


class ExtensionArchivo(models.Model):
    """Una extensión admitida al subir archivos.

    Antes era una lista escrita en el código, así que añadir un tipo de archivo
    exigía tocar el repositorio y desplegar. Ahora se administra desde el panel.

    Desactivar en vez de borrar es lo habitual: deja de admitirse para subidas
    nuevas, pero queda el registro de que estuvo permitida, que es lo que
    explica los archivos ya cargados con esa extensión.
    """

    extension = models.CharField(
        max_length=16,
        unique=True,
        verbose_name="Extensión",
        help_text="Con o sin punto, en cualquier caja: se guarda como «.pdf».",
    )
    activa = models.BooleanField(
        default=True,
        verbose_name="¿Activa?",
        help_text="Si se desactiva, deja de admitirse en cargues nuevos.",
    )
    descripcion = models.CharField(
        max_length=120, blank=True, verbose_name="Descripción",
        help_text="Para quién administra: «Documento de Word», «Hoja de cálculo»…",
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Extensión de archivo permitida"
        verbose_name_plural = "Extensiones de archivo permitidas"
        ordering = ("extension",)

    def __str__(self):
        return self.extension

    def clean(self):
        """Normaliza a «.ext» en minúsculas.

        Sin esto, «PDF», «.pdf» y «.PDF» serían tres filas distintas y la
        restricción de unicidad no serviría de nada: la comparación del
        validador se hace sobre la extensión en minúsculas y con punto.
        """
        valor = (self.extension or "").strip().lower().lstrip(".")
        if not valor:
            raise ValidationError({"extension": "Escribe la extensión, por ejemplo «pdf»."})
        if not valor.isalnum():
            raise ValidationError(
                {"extension": "Usa solo letras y números, sin puntos intermedios."}
            )
        self.extension = f".{valor}"
        super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class AdjuntoDeRevision(models.Model):
    """Documento que adjunta quien revisa, en cualquiera de los dos dominios.

    Quien revisa no tenía dónde dejar un soporte de lo que observa: solo podía
    escribir un comentario. Esto le permite acompañarlo con un archivo (una
    captura, una norma, un formato corregido), y es **opcional**: el formulario
    de revisión no lo exige.

    **Cuelga del objeto revisado, no de la revisión**, y esa es la decisión que
    hace que funcione: cuando el revisor está escribiendo el formulario la
    revisión todavía no existe, así que no habría a qué atar el archivo. Atado a
    la versión (`DocumentoEntrega`) o a la entrega (`ActividadEntrega`) más el
    usuario, el adjunto se puede subir antes de registrar la revisión, que es
    justo como se usa.

    Vive en `cuentas` porque los `models.py` de los dos dominios son
    definitivos, y porque así un solo modelo sirve a los dos.
    """

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, verbose_name="Tipo de objeto"
    )
    object_id = models.PositiveIntegerField(verbose_name="Id del objeto")
    revisado = GenericForeignKey("content_type", "object_id")

    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="Quien lo adjuntó",
        related_name="adjuntos_de_revision",
    )
    nombre = models.CharField(max_length=250, verbose_name="Nombre del documento")
    archivo = models.FileField(
        verbose_name="Archivo", upload_to="revisiones/%Y/%m/"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Adjunto de revisión"
        verbose_name_plural = "Adjuntos de revisión"
        ordering = ("fecha_creacion",)
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.nombre} ({self.usuario})"

    @classmethod
    def de(cls, objeto):
        """Adjuntos de un objeto revisado, con su autor ya cargado."""
        return cls.objects.filter(
            content_type=ContentType.objects.get_for_model(objeto),
            object_id=objeto.pk,
        ).select_related("usuario")
