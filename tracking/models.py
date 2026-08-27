"""Modelos de tracking.

Los usuarios del dashboard son hardcodeados en settings.DASHBOARD_USUARIOS
(ver tracking.middleware); aquí solo vive la flota.
"""

from django.db import models


class FlotaVehiculo(models.Model):
    """Un vehículo de la flota, según la planilla que entrega Jhon.

    Complementa VEHICULOS_INFO en services.py: aquí vive la placa, el
    contrato (empresa) y la ruta asignada, que el API de Service24GPS no
    reporta.
    """

    placa = models.CharField(max_length=10, unique=True)
    no_interno = models.CharField(max_length=10, db_index=True)
    ciudad = models.CharField(max_length=60, blank=True)
    tipo_vehiculo = models.CharField(max_length=30, blank=True)
    marca = models.CharField(max_length=40, blank=True)
    modelo = models.CharField(max_length=10, blank=True)
    capacidad_sillas = models.PositiveSmallIntegerField(null=True, blank=True)
    contrato = models.CharField(max_length=40, blank=True)
    ruta = models.CharField(max_length=40, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'vehículo de flota'
        verbose_name_plural = 'flota (vehículos)'
        ordering = ['no_interno']

    def __str__(self):
        return f'{self.placa} (INT {self.no_interno})'
