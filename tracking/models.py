"""Los usuarios del dashboard: quién puede entrar y qué empresas ve.

Antes vivían hardcodeados en settings.DASHBOARD_USUARIOS; ahora son filas de
esta tabla, editables desde /admin/ sin redeploy.
"""

from django.contrib.auth.hashers import check_password, make_password
from django.db import models

from . import services


class DashboardUsuario(models.Model):
    """Una cuenta del dashboard.

    `empresas` en blanco significa acceso total (todas las pestañas y el
    mapa de flota), igual que `empresas: None` en el dict de antes.
    """

    usuario = models.CharField(
        max_length=254, unique=True,
        help_text='Nombre de usuario, o correo para las cuentas invitadas.',
    )
    clave_hash = models.CharField(
        max_length=128, blank=True,
        help_text='En blanco mientras la invitación no ha sido reclamada.',
    )
    empresas = models.CharField(
        max_length=200, blank=True,
        help_text=('Lista separada por comas de entre '
                    f'{", ".join(services.EMPRESAS[:-1])} (vacío = todas).'),
    )
    activo = models.BooleanField(default=True)
    puede_invitar = models.BooleanField(
        default=False,
        help_text='Puede dar de alta invitaciones para otras cuentas.',
    )
    invitado_por = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='invitados',
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'usuario del dashboard'
        verbose_name_plural = 'usuarios del dashboard'

    def __str__(self):
        return self.usuario

    def save(self, *args, **kwargs):
        self.usuario = self.usuario.strip().lower()
        super().save(*args, **kwargs)

    def set_clave(self, clave_en_claro):
        self.clave_hash = make_password(clave_en_claro)

    def check_clave(self, clave_en_claro):
        return bool(self.clave_hash) and check_password(clave_en_claro, self.clave_hash)

    @property
    def reclamada(self):
        """Dice si la invitación ya fue activada (tiene contraseña propia)."""
        return bool(self.clave_hash)

    @property
    def empresas_tuple(self):
        """Tupla de empresas permitidas, o None si el usuario ve todas."""
        if not self.empresas.strip():
            return None
        return tuple(e.strip().upper() for e in self.empresas.split(',') if e.strip())


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
