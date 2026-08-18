"""Importa la planilla de flota (xlsx) a la tabla FlotaVehiculo.

Uso:
    python manage.py import_flota_xlsx "FLOTA JHON RASTRELITAL (1).xlsx"
"""

import openpyxl
from django.core.management.base import BaseCommand, CommandError

from tracking.models import FlotaVehiculo

COLUMNAS = {
    'Placa': 'placa',
    'No Interno': 'no_interno',
    'Ciudad': 'ciudad',
    'Tipo de Vehículo': 'tipo_vehiculo',
    'Marca': 'marca',
    'Modelo': 'modelo',
    'Capacidad en Sillas': 'capacidad_sillas',
    'CONTRATO': 'contrato',
    'RUTA': 'ruta',
}


class Command(BaseCommand):
    help = 'Importa/actualiza la flota desde un xlsx con columnas Placa, No Interno, Ciudad, etc.'

    def add_arguments(self, parser):
        parser.add_argument('archivo', help='Ruta al archivo .xlsx')

    def handle(self, *args, **options):
        ruta = options['archivo']
        try:
            wb = openpyxl.load_workbook(ruta, data_only=True)
        except FileNotFoundError:
            raise CommandError(f'No se encontró el archivo: {ruta}')

        ws = wb.worksheets[0]
        filas = list(ws.iter_rows(values_only=True))
        if not filas:
            raise CommandError('El archivo está vacío.')

        encabezado = [str(c).strip() if c else '' for c in filas[0]]
        campos_por_col = {}
        for i, nombre in enumerate(encabezado):
            campo = COLUMNAS.get(nombre.rstrip(' \t'))
            if campo:
                campos_por_col[i] = campo

        faltantes = set(COLUMNAS.values()) - set(campos_por_col.values())
        if faltantes:
            self.stdout.write(self.style.WARNING(
                f'Columnas no encontradas en el xlsx (se dejan vacías): {faltantes}'
            ))

        creados = actualizados = omitidos = 0
        for fila in filas[1:]:
            datos = {}
            for i, campo in campos_por_col.items():
                valor = fila[i] if i < len(fila) else None
                if isinstance(valor, str):
                    valor = valor.strip()
                datos[campo] = valor

            placa = (datos.get('placa') or '').strip().upper()
            if not placa:
                omitidos += 1
                continue

            datos['placa'] = placa
            datos['no_interno'] = str(datos.get('no_interno') or '').strip()
            datos['contrato'] = (datos.get('contrato') or '').strip().upper()
            datos['ruta'] = (datos.get('ruta') or '') or ''
            datos['modelo'] = str(datos.get('modelo') or '').strip()

            capacidad = datos.get('capacidad_sillas')
            try:
                datos['capacidad_sillas'] = int(capacidad) if capacidad not in (None, '') else None
            except (TypeError, ValueError):
                datos['capacidad_sillas'] = None

            _, creado = FlotaVehiculo.objects.update_or_create(
                placa=placa, defaults=datos,
            )
            if creado:
                creados += 1
            else:
                actualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f'Listo: {creados} creados, {actualizados} actualizados, {omitidos} filas sin placa omitidas.'
        ))
