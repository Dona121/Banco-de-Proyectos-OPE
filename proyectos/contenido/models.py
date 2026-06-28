from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

# Create your models here.

class Fechas(models.Model):
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class Proyectos(Fechas):
    nombre = models.TextField(null=False,blank=False,verbose_name="Nombre del proyecto")
    creador_por = models.ForeignKey(
        User,null=False,blank=False, verbose_name="Creado por",on_delete=models.CASCADE,related_name="proyectos_creados"
    )
    asignado_a = models.ForeignKey(
        User,null=False,blank=False, verbose_name="Asigando a",on_delete=models.CASCADE,related_name="proyectos_asignados"
    )

    class Meta:
        verbose_name = "Proyecto"
        verbose_name_plural = "Proyectos"

    def __str__(self):
        return f"{self.nombre}"

class Actividades(Fechas):
    class EstadoActividad(models.TextChoices):
        PENDIENTE = "PE", "Pendiente"
        EN_REVISION = "ER", "En revisión"
        AJUSTES = "AJ", "Requiere ajustes"
        APROBADA = "AP", "Aprobada"
    proyecto = models.ForeignKey(Proyectos,null=False,blank=False,on_delete=models.CASCADE)
    nombre = models.TextField(null=False,blank=False,verbose_name="Nombre de la actividad")
    fecha_programada = models.DateTimeField(null=False,blank=False,verbose_name="Fecha programada")
    fecha_vencimiento = models.DateTimeField(null=False,blank=False,verbose_name="Fecha de vencimiento")
    estado = models.CharField(max_length=2,null=False,blank=False,choices=EstadoActividad.choices)
    asignado_por = models.ForeignKey(
        User,null=False,blank=False,verbose_name="Asigando por",on_delete=models.CASCADE,related_name="actividades_asignadas_por"
    )
    asignado_a = models.ForeignKey(
        User,null=False,blank=False,verbose_name="Asignado a",on_delete=models.CASCADE,related_name="actividades_asignadas_a"
    )

    class Meta:
        verbose_name = "Actividad"
        verbose_name_plural = "Actividades"

    def __str__(self):
        return f"{self.nombre}"
    
    def clean(self):
        if self.fecha_programada > self.fecha_vencimiento:
            raise ValidationError("La fecha programada no puede ser mayor a la fecha de vencimiento")
        super().clean()
    
class Subactividades(Fechas):
    nombre = models.TextField(null=False,blank=False,verbose_name="Nombre de la subactividad")
    actividad = models.ForeignKey(Actividades,null=False,blank=False,on_delete=models.CASCADE,verbose_name="Actividad")

    class Meta:
        verbose_name = "Subactividad"
        verbose_name_plural = "Subactividades"
    
    def __str__(self):
        return f"{self.id}: {self.nombre}"
    
class ActividadEntrega(Fechas):
    actividad = models.ForeignKey(Actividades,null=False,blank=False,on_delete=models.CASCADE,verbose_name="Actividad")
    numero_version = models.IntegerField(null=False,blank=False,verbose_name="Número de la versión")
    usuario = models.ForeignKey(User,null=False,blank=False,verbose_name="Usuario que entregó",on_delete=models.CASCADE)
    comentario = models.TextField(null=False,blank=False,verbose_name="Comentario")

    class Meta:
        verbose_name = "Actividad entrega"
        verbose_name_plural = "Actividades entrega"
        constraints = [
            models.UniqueConstraint(fields=["actividad","numero_version"],name="Actividad_version_unique")
        ]

    def __str__(self):
        return f"{self.actividad.nombre}, versión: {self.numero_version}"
    
    def save(self,*args,**kwargs):
        if not self.pk:
            ultima = (
                ActividadEntrega.objects.filter(
                    actividad=self.actividad
                ).order_by("-numero_version")
                .first()
            )
            self.numero_version = (
                ultima.numero_version + 1 if ultima else 1
            )
        self.full_clean()
        super().save(*args,**kwargs)

    def clean(self):
        if self.actividad.estado == Actividades.EstadoActividad.APROBADA:
            raise ValidationError("No se puede crear una entrega para una actividad aprobada")
        super().clean()

class Documentos(Fechas):
    actividad_entrega = models.ForeignKey(ActividadEntrega,null=False,blank=False,verbose_name="Actividad entrega",on_delete=models.CASCADE)
    nombre = models.CharField(max_length=250,null=False,blank=False,verbose_name="Nombre del documento")
    archivo = models.FileField(verbose_name="Archivo")

    class Meta:
        verbose_name = "Documento"
        verbose_name_plural ="Documentos"
    
    def __str__(self):
        return f"{self.actividad_entrega}: {self.nombre}"
    
class Revisiones(Fechas):
    class ResultadoRevision(models.TextChoices):
        APROBADA = "AP", "Aprobada"
        AJUSTES = "AJ", "Requiere ajustes"
        RECHAZADA = "RE", "Rechazada"
    actividad_entrega = models.OneToOneField(ActividadEntrega,null=False,blank=False,verbose_name="Actividad entrega",on_delete=models.CASCADE)
    revisor = models.ForeignKey(User,null=False,blank=False,verbose_name="Revisor",on_delete=models.CASCADE)
    comentario = models.TextField(null=False,blank=False,verbose_name="Comentario")
    resultado = models.CharField(max_length=2,choices=ResultadoRevision.choices)

    class Meta:
        verbose_name = "Revisión"
        verbose_name_plural = "Revisiones"
    
    def __str__(self):
        return f"Revisión: {self.id}, realizada por {self.revisor.username}"
    
    def clean(self):
        estado = self.actividad_entrega.actividad.estado

        if estado and estado == Actividades.EstadoActividad.APROBADA:
            raise ValidationError("No se pueden crear revisiones sobre una actividad aprobada")
        super().clean()
    
    def save(self,*args,**kwargs):
        self.full_clean()
        super().save(*args,**kwargs)