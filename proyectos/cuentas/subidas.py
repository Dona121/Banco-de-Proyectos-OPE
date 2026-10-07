"""Subida de archivos directa al bucket, sin pasar por el servidor.

Por qué no se suben a través de Django: el archivo viajaba navegador → contenedor
→ Supabase, y con el arranque actual (un worker sync, `--timeout` de serie) eso
limita las subidas a lo que quepa en 30 segundos y deja la plataforma sin
responder a todos los demás mientras dura. Un archivo de 1 GB (el techo del
bucket) son 27 minutos a 5 Mbps. Así el archivo va directo y el servidor solo
firma el permiso y registra el resultado.

El flujo tiene tres pasos:

1. El navegador pide permiso (``firmar``). El servidor comprueba quién es y qué
   puede hacer, arma la clave del objeto y devuelve una URL firmada más un token.
2. El navegador sube el archivo a esa URL con ``XMLHttpRequest``, que es lo que
   permite mostrar el porcentaje de avance.
3. El navegador confirma (``confirmar``). El servidor verifica el token, mira en
   el bucket que el objeto esté ahí y cuánto pesa, y recién entonces crea el
   registro en la base.

El token va firmado con ``django.core.signing``: sin él, el paso 3 aceptaría
cualquier clave que el navegador quisiera inventarse, y con ella se podría
asociar a una cuenta un archivo que vive en otra parte del bucket.
"""
import uuid
from datetime import datetime

from django.core import signing
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage

from .validadores import TAMANO_MAXIMO, nombre_seguro

# Validez de la URL firmada. Tiene que cubrir una subida lenta y larga: 1 GB a
# 3 Mbps son unos 45 minutos.
VIGENCIA_FIRMA = 3 * 60 * 60

# El token de confirmación solo tiene que sobrevivir a la subida.
VIGENCIA_TOKEN = VIGENCIA_FIRMA

# Un enlace de descarga se usa en cuanto se pulsa; no hace falta que dure.
VIGENCIA_DESCARGA = 5 * 60

_SAL = "cuentas.subidas"


def _cliente_y_bucket():
    """Cliente boto3 y nombre del bucket, tomados del storage ya configurado.

    Se reutiliza el del storage en vez de crear otro para no repetir el endpoint,
    la región y el estilo de firma, que son específicos de Supabase y ya están
    resueltos en ``settings``.
    """
    bucket = getattr(default_storage, "bucket", None)
    if bucket is None:
        raise ValidationError(
            "La subida directa necesita el almacenamiento en Supabase; el "
            "almacenamiento configurado no es compatible."
        )
    return bucket.meta.client, default_storage.bucket_name


def clave_de_objeto(prefijo, nombre_archivo):
    """Clave del objeto dentro del bucket, con el mismo formato que `upload_to`.

    Se le añade un fragmento aleatorio porque dos personas pueden subir archivos
    con el mismo nombre y aquí no está el renombrado automático que hace Django
    cuando guarda un `FileField`.
    """
    nombre = nombre_seguro(nombre_archivo)
    hoy = datetime.now()
    carpeta = prefijo.replace("%Y", f"{hoy:%Y}").replace("%m", f"{hoy:%m}")
    if carpeta and not carpeta.endswith("/"):
        carpeta += "/"
    return f"{carpeta}{uuid.uuid4().hex[:8]}_{nombre}"


def firmar_subida(*, prefijo, nombre_archivo, content_type, contexto):
    """Prepara una subida y devuelve lo que el navegador necesita.

    ``contexto`` es lo que el paso de confirmación tendrá que recordar (de qué
    cuenta es, qué tipo de documento), y viaja dentro del token firmado.
    """
    cliente, bucket = _cliente_y_bucket()
    relativa = clave_de_objeto(prefijo, nombre_archivo)
    # `default_storage.location` es el prefijo del storage ("media"); la clave del
    # objeto lo lleva, pero lo que se guarda en el FileField va sin él.
    base = (default_storage.location or "").strip("/")
    completa = f"{base}/{relativa}" if base else relativa

    url = cliente.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": completa, "ContentType": content_type},
        ExpiresIn=VIGENCIA_FIRMA,
        HttpMethod="PUT",
    )
    token = signing.dumps(
        {"clave": relativa, "completa": completa, **contexto}, salt=_SAL
    )
    return {
        "url": url,
        "metodo": "PUT",
        "content_type": content_type,
        "token": token,
        "nombre": relativa.rsplit("/", 1)[-1],
        "tamano_maximo": TAMANO_MAXIMO,
    }


def url_de_descarga(campo, nombre_visible):
    """URL firmada que **descarga** el archivo con un nombre legible.

    El enlace normal de un `FileField` abre el archivo en el navegador y con el
    nombre con que quedó guardado en el bucket (lleva un fragmento aleatorio para
    que dos archivos iguales no choquen). Aquí se firma la descarga pidiéndole al
    almacenamiento la cabecera `Content-Disposition`, que es lo que hace que el
    navegador lo baje y con el nombre que le demos.

    No vale el atributo `download` de un enlace: el archivo vive en otro dominio
    (el del bucket) y los navegadores lo ignoran para enlaces de otro origen.
    """
    if not campo:
        raise ValidationError("Ese archivo no está disponible.")
    cliente, bucket = _cliente_y_bucket()
    base = (default_storage.location or "").strip("/")
    clave = f"{base}/{campo.name}" if base else campo.name
    # El nombre viaja en una cabecera HTTP: tiene que ser ASCII.
    nombre = nombre_seguro(nombre_visible or campo.name.rsplit("/", 1)[-1])
    return cliente.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": clave,
            "ResponseContentDisposition": f'attachment; filename="{nombre}"',
        },
        ExpiresIn=VIGENCIA_DESCARGA,
    )


def confirmar_subida(token, *, tamano_maximo=TAMANO_MAXIMO):
    """Valida el token y comprueba el objeto en el bucket. Devuelve el contexto.

    Mirar el bucket no es un lujo: es lo que impide que alguien confirme un
    archivo que nunca subió, o que se salte el límite de tamaño, que en una URL
    firmada de tipo PUT no se puede imponer de antemano. Si el archivo excede el
    máximo se borra, para no dejar basura ocupando el bucket.
    """
    try:
        datos = signing.loads(token, salt=_SAL, max_age=VIGENCIA_TOKEN)
    except signing.SignatureExpired:
        raise ValidationError(
            "La subida tardó demasiado y el permiso caducó. Vuelve a intentarlo."
        )
    except signing.BadSignature:
        raise ValidationError("La confirmación de la subida no es válida.")

    cliente, bucket = _cliente_y_bucket()
    try:
        cabecera = cliente.head_object(Bucket=bucket, Key=datos["completa"])
    except Exception:
        raise ValidationError(
            "El archivo no llegó al almacenamiento. Vuelve a subirlo."
        )

    tamano = cabecera.get("ContentLength", 0)
    if tamano > tamano_maximo:
        cliente.delete_object(Bucket=bucket, Key=datos["completa"])
        raise ValidationError(
            f"El archivo pesa {tamano / 1024 / 1024:.0f} MB y el máximo es "
            f"{tamano_maximo / 1024 / 1024:.0f} MB."
        )
    if tamano == 0:
        cliente.delete_object(Bucket=bucket, Key=datos["completa"])
        raise ValidationError("El archivo llegó vacío. Vuelve a subirlo.")

    datos["tamano"] = tamano
    return datos
