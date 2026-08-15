"""Migra las 4 cuentas históricas (env vars) a DashboardUsuario.

Uso único tras el primer `migrate` contra la base nueva. Es idempotente: si
una cuenta ya existe solo actualiza su clave y empresas, no crea duplicados.
Después de correrlo, las cuentas se administran desde /admin/, no desde
variables de entorno.
"""

import os

from django.core.management.base import BaseCommand

from tracking.models import DashboardUsuario

CUENTAS = (
    ('admin', 'DASHBOARD_CLAVE_ADMIN', 'Admin', ''),
    ('procaps', 'DASHBOARD_CLAVE_PROCAPS', 'Procaps', 'PROCAPS'),
    ('ditar', 'DASHBOARD_CLAVE_DITAR', 'Ditar', 'DITAR'),
    ('relianz', 'DASHBOARD_CLAVE_RELIANZ', 'relianz', 'RELIANZ'),
)


class Command(BaseCommand):
    help = 'Crea o actualiza las cuentas históricas del dashboard en la base de datos.'

    def handle(self, *args, **options):
        for usuario, env_var, default_clave, empresas in CUENTAS:
            clave = os.getenv(env_var, default_clave)
            cuenta, creada = DashboardUsuario.objects.get_or_create(
                usuario=usuario, defaults={'empresas': empresas})
            cuenta.empresas = empresas
            cuenta.set_clave(clave)
            cuenta.save()
            accion = 'creada' if creada else 'actualizada'
            self.stdout.write(self.style.SUCCESS(f'Cuenta "{usuario}" {accion}.'))
