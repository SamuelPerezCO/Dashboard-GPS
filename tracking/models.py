"""Modelos de tracking: las cuentas del dashboard y la flota.

Las cuentas viven en esta tabla y se administran desde /admin/, sin
redeploy. settings.DASHBOARD_USUARIOS sigue existiendo, pero ya solo como
acceso de emergencia (ver tracking.middleware).
"""

from django.contrib.auth.hashers import check_password, make_password
from django.db import models

from . import services

EMPRESAS_ASIGNABLES = tuple(e for e in services.EMPRESAS
                            if e != services.TAB_SIN_IDENTIFICAR)


class DashboardUsuario(models.Model):
    """Una cuenta del dashboard: una persona, con su correo y su clave.

    Se entra con el correo completo. La clave se guarda hasheada con los
    hashers de Django, así que ni el admin ni la base la muestran en claro.

    El acceso se reparte con `acceso_total` y `empresas`. Son dos campos y
    no uno solo porque «sin empresas marcadas» tiene que significar *no ve
    nada*, no *lo ve todo*: si el vacío diera acceso total, una cuenta a la
    que se le olvidó marcar la empresa acabaría viéndolo todo.
    """

    correo = models.EmailField(
        unique=True, verbose_name='correo',
        help_text='Con este correo entra al dashboard.',
    )
    nombre = models.CharField(
        max_length=80, blank=True,
        help_text='Opcional, para saber de quién es la cuenta.',
    )
    clave_hash = models.CharField(max_length=128, verbose_name='clave')
    acceso_total = models.BooleanField(
        default=False, verbose_name='acceso total',
        help_text='Ve todas las empresas y además el mapa de flota.',
    )
    empresas = models.CharField(
        max_length=200, blank=True,
        help_text=('Empresas que ve, separadas por comas: '
                   f'{", ".join(EMPRESAS_ASIGNABLES)}. '
                   'Se ignora si tiene acceso total.'),
    )
    activo = models.BooleanField(
        default=True,
        help_text='Al desmarcarlo la cuenta deja de entrar, y la sesión que '
                  'tuviera abierta se cierra sola en la siguiente página.',
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    ultimo_ingreso = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'usuario del dashboard'
        verbose_name_plural = 'usuarios del dashboard'
        ordering = ['correo']

    def __str__(self):
        return self.correo

    def save(self, *args, **kwargs):
        self.correo = self.correo.strip().lower()
        super().save(*args, **kwargs)

    def set_clave(self, clave_en_claro):
        """Guarda la clave hasheada. No la escribe en claro en ningún lado."""
        self.clave_hash = make_password(clave_en_claro)

    def check_clave(self, clave_en_claro):
        """Dice si la clave sirve. Una cuenta sin hash no entra con nada."""
        if not self.clave_hash or not clave_en_claro:
            return False
        return check_password(clave_en_claro, self.clave_hash)

    @property
    def empresas_tuple(self):
        """Techo de la cuenta: None si lo ve todo, si no las suyas.

        Devuelve lo mismo que la clave 'empresas' del catálogo de settings,
        para que el resto del sitio no tenga que saber de dónde salió la
        cuenta.
        """
        if self.acceso_total:
            return None
        return tuple(e.strip().upper()
                     for e in self.empresas.split(',') if e.strip())

    @property
    def ficha(self):
        """La cuenta con la forma que espera el resto del sitio."""
        return {'empresas': self.empresas_tuple}


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
