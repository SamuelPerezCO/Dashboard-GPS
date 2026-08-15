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

    usuario = models.CharField(max_length=32, unique=True)
    clave_hash = models.CharField(max_length=128)
    empresas = models.CharField(
        max_length=200, blank=True,
        help_text=('Lista separada por comas de entre '
                    f'{", ".join(services.EMPRESAS[:-1])} (vacío = todas).'),
    )
    activo = models.BooleanField(default=True)
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
        return check_password(clave_en_claro, self.clave_hash)

    @property
    def empresas_tuple(self):
        """Tupla de empresas permitidas, o None si el usuario ve todas."""
        if not self.empresas.strip():
            return None
        return tuple(e.strip().upper() for e in self.empresas.split(',') if e.strip())
