"""Validación y saneado de los archivos que suben los usuarios.

Ningún formulario con ``FileField`` comprobaba nada: se podía subir un ejecutable
de 400 MB al bucket y quedaba ahí, con el nombre que trajera. Estos validadores
se aplican en los formularios (no en los modelos, que en cuentas de cobro son
definitivos).
"""
import re
import unicodedata
from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils.text import get_valid_filename

# Respaldo del conjunto admitido, en minúsculas y con punto. La lista que manda
# vive en la base (`cuentas.models.ExtensionArchivo`), administrable desde el
# panel; esto es lo que se usa si esa tabla está vacía, para que un borrado
# accidental no deje la plataforma sin poder subir ningún archivo.
EXTENSIONES_POR_OMISION = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods",
    ".jpg", ".jpeg", ".png", ".webp", ".zip",
})

# Caché de proceso: `validar_extension` se llama en cada firma de subida y en
# cada formulario con archivo, y la lista cambia muy de vez en cuando. La
# invalidan las señales de `cuentas/apps.py` al guardar o borrar una extensión.
_cache_extensiones = None


def limpiar_cache_de_extensiones():
    """Olvida la lista cacheada. La llaman las señales de `apps.py`."""
    global _cache_extensiones
    _cache_extensiones = None


def extensiones_permitidas():
    """Extensiones activas, de la base; el conjunto del código si no hay ninguna.

    Durante las migraciones iniciales la tabla puede no existir todavía, y en ese
    caso tampoco hay nada que validar: se responde con el respaldo en vez de
    reventar.
    """
    global _cache_extensiones
    if _cache_extensiones is not None:
        return _cache_extensiones
    try:
        from .models import ExtensionArchivo

        activas = frozenset(
            ExtensionArchivo.objects.filter(activa=True).values_list(
                "extension", flat=True
            )
        )
    except Exception:
        return EXTENSIONES_POR_OMISION
    _cache_extensiones = activas or EXTENSIONES_POR_OMISION
    return _cache_extensiones

# Tamaño máximo por archivo. Suficiente para un PDF escaneado y lejos de lo que
# tarda en subirse por una conexión mala.
# El bucket de Supabase está configurado a 1 GB por archivo; la validación del
# servidor acompaña ese techo.
TAMANO_MAXIMO_MB = 1024
TAMANO_MAXIMO = TAMANO_MAXIMO_MB * 1024 * 1024

# Hasta dónde se recorta el nombre (sin contar la extensión). La clave completa en
# S3 admite mucho más, pero un nombre kilométrico no aporta nada y estorba.
LARGO_MAXIMO_NOMBRE = 80


def nombre_seguro(nombre):
    """Nombre de archivo en ASCII, apto para usarse como clave en el bucket.

    Un archivo llamado "Documentación.pdf" hacía fallar la subida con un 500: el
    nombre llega sin tocar hasta boto3 (django-storages solo normaliza los
    separadores de ruta) y la clave con caracteres no ASCII rompe contra el
    gateway S3 de Supabase, que se usa con firma v4 y rutas estilo "path".

    Se translitera en vez de percent-encodear porque el problema reaparecería en
    las descargas y en las URLs firmadas; y en vez de renombrar a un UUID, porque
    así el nombre sigue siendo legible para quien administre el bucket.

    "Documentación.pdf" → "Documentacion.pdf"
    """
    base = Path(nombre or "").name
    sufijo = Path(base).suffix.lower()
    cuerpo = base[: len(base) - len(sufijo)] if sufijo else base

    def a_ascii(texto):
        return (
            unicodedata.normalize("NFKD", texto)
            .encode("ascii", "ignore")
            .decode("ascii")
        )

    cuerpo = a_ascii(cuerpo)
    if cuerpo.strip():
        # `get_valid_filename` revienta con una cadena vacía, de ahí la guarda.
        cuerpo = get_valid_filename(cuerpo)
    # Los caracteres que desaparecen al transliterar (un guión largo, una comilla
    # tipográfica) dejan separadores de relleno: "Ano_2026__informe".
    cuerpo = re.sub(r"[_.-]{2,}", "_", cuerpo)[:LARGO_MAXIMO_NOMBRE].strip("._-")
    # Un nombre escrito por completo en un alfabeto no latino se queda sin nada
    # que transliterar: mejor un nombre genérico que una clave vacía.
    return f"{cuerpo or 'archivo'}{a_ascii(sufijo)}"


def validar_extension(nombre):
    """Valida la extensión a partir del nombre, sin necesitar el archivo.

    La subida directa al bucket tiene que decidir antes de firmar el permiso,
    cuando todavía no hay ningún archivo en el servidor.
    """
    permitidas = extensiones_permitidas()
    extension = Path(nombre or "").suffix.lower()
    if extension not in permitidas:
        admitidas = ", ".join(sorted(e.lstrip(".") for e in permitidas))
        raise ValidationError(
            f"«{extension or nombre}» no es un tipo de archivo admitido. "
            f"Usa uno de estos: {admitidas}."
        )
    return extension


def validar_archivo(archivo):
    """Valida extensión y tamaño de un archivo subido."""
    if archivo is None:
        return archivo
    validar_extension(archivo.name)
    tamano = getattr(archivo, "size", 0) or 0
    if tamano > TAMANO_MAXIMO:
        raise ValidationError(
            f"El archivo pesa {tamano / 1024 / 1024:.1f} MB y el máximo es "
            f"{TAMANO_MAXIMO_MB} MB. Comprímelo o divídelo antes de subirlo."
        )
    return archivo


class ArchivoValidadoMixin:
    """Aplica ``validar_archivo`` a los campos de archivo del formulario.

    Declara los campos en ``campos_de_archivo``; por defecto valida todos los que
    reciban un archivo subido.
    """

    campos_de_archivo = ()

    def _campos_a_validar(self):
        if self.campos_de_archivo:
            return self.campos_de_archivo
        from django import forms

        return [
            nombre
            for nombre, campo in self.fields.items()
            if isinstance(campo, forms.FileField)
        ]

    def clean(self):
        cleaned = super().clean()
        for nombre in self._campos_a_validar():
            archivo = cleaned.get(nombre)
            if archivo is None or not hasattr(archivo, "name"):
                continue
            try:
                validar_archivo(archivo)
            except ValidationError as exc:
                self.add_error(nombre, exc)
                continue
            # Renombrar aquí y no en el modelo (en cuentas de cobro es
            # definitivo) ni en el storage: así los cuatro formularios con
            # archivo quedan cubiertos en un solo sitio.
            archivo.name = nombre_seguro(archivo.name)
        return cleaned


class ArchivoValidadoAdminMixin:
    """Lleva ``ArchivoValidadoMixin`` a los formularios del Django Admin.

    El admin genera sus formularios solos, así que no heredaban la validación:
    quien tuviera acceso podía subir un ejecutable o un archivo enorme por ahí,
    justo lo que los cuatro formularios de la aplicación sí impiden. El admin es
    una herramienta de soporte, no una puerta trasera.

    Se mezcla sobre la clase que construye el admin en vez de declarar un
    ``form`` por modelo: así vale igual para los cuatro y no hay que recordar
    añadirlo al siguiente.
    """

    @staticmethod
    def _con_validacion(base):
        return type(base.__name__, (ArchivoValidadoMixin, base), {})

    def get_form(self, request, obj=None, **kwargs):
        return self._con_validacion(super().get_form(request, obj, **kwargs))


class ArchivoValidadoInlineMixin(ArchivoValidadoAdminMixin):
    """Lo mismo para los inlines (los documentos cuelgan de una entrega)."""

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form = self._con_validacion(formset.form)
        return formset
