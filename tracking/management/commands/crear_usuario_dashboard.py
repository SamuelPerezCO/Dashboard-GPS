"""Da de alta (o actualiza) una cuenta del dashboard desde la consola.

Hace falta para la primera cuenta: el admin de Django pide su propio
superusuario, así que sin esto no habría por dónde empezar en un servidor
recién desplegado. También sirve para arreglarle la clave a alguien sin
abrir el navegador.

    python manage.py crear_usuario_dashboard jefe@procaps.com.co \\
        --nombre "Jefe de logística" --empresas PROCAPS
    python manage.py crear_usuario_dashboard samuel@rastrelital.com --acceso-total
    python manage.py crear_usuario_dashboard alguien@x.com --desactivar
"""

import getpass

from django.core.management.base import BaseCommand, CommandError

from tracking.models import EMPRESAS_ASIGNABLES, DashboardUsuario


class Command(BaseCommand):
    help = 'Crea o actualiza una cuenta del dashboard.'

    def add_arguments(self, parser):
        parser.add_argument('correo', help='Correo con el que entra.')
        parser.add_argument(
            '--clave',
            help='Si no se pasa, se pide por consola sin mostrarla. Pasarla '
                 'aquí la deja escrita en el historial del shell.')
        parser.add_argument('--nombre', default='',
                            help='De quién es la cuenta. Opcional.')
        parser.add_argument(
            '--empresas', default='',
            help='Separadas por comas, de entre '
                 f'{", ".join(EMPRESAS_ASIGNABLES)}.')
        parser.add_argument('--acceso-total', action='store_true',
                            help='Ve todas las empresas y el mapa de flota.')
        parser.add_argument('--desactivar', action='store_true',
                            help='Le quita el acceso a una cuenta que ya existe.')

    def handle(self, *args, **opciones):
        correo = (opciones['correo'] or '').strip().lower()
        if '@' not in correo:
            raise CommandError(
                f'{correo!r} no es un correo: al dashboard se entra con el '
                f'correo completo.')

        usuario = DashboardUsuario.objects.filter(correo=correo).first()

        if opciones['desactivar']:
            if usuario is None:
                raise CommandError(f'No hay ninguna cuenta con el correo {correo}.')
            usuario.activo = False
            usuario.save(update_fields=['activo'])
            self.stdout.write(self.style.SUCCESS(
                f'{correo} queda desactivado: su sesión se cierra sola en la '
                f'siguiente página que abra.'))
            return

        empresas = [e.strip().upper()
                    for e in (opciones['empresas'] or '').split(',') if e.strip()]
        desconocidas = [e for e in empresas if e not in EMPRESAS_ASIGNABLES]
        if desconocidas:
            raise CommandError(
                f'Empresa desconocida: {", ".join(desconocidas)}. '
                f'Usa las de {", ".join(EMPRESAS_ASIGNABLES)}.')

        acceso_total = opciones['acceso_total']
        if not acceso_total and not empresas:
            raise CommandError(
                'Dale --empresas o --acceso-total: sin ninguna de las dos la '
                'cuenta entraría a un dashboard vacío.')

        clave = opciones['clave']
        if not clave and usuario is None:
            clave = getpass.getpass(f'Clave para {correo}: ')
            if clave != getpass.getpass('Repítela: '):
                raise CommandError('Las dos claves no coinciden.')
        if not clave and usuario is None:
            raise CommandError('La clave no puede quedar vacía.')

        nuevo = usuario is None
        if nuevo:
            usuario = DashboardUsuario(correo=correo)
        usuario.nombre = opciones['nombre'] or usuario.nombre
        usuario.acceso_total = acceso_total
        usuario.empresas = ','.join(empresas)
        usuario.activo = True
        if clave:
            usuario.set_clave(clave)
        usuario.save()

        ve = 'todo, y el mapa de flota' if acceso_total else ', '.join(empresas)
        self.stdout.write(self.style.SUCCESS(
            f'{"Creada" if nuevo else "Actualizada"} la cuenta {correo} (ve {ve}).'))
