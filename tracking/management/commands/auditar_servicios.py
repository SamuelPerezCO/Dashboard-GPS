"""Audita las geocercas: encuentra los buses a los que la geocerca no dispara.

La ocupación es timbradas / (servicios × capacidad), y desde que los servicios
se cuentan por tandas de timbradas el denominador ya no depende de la geocerca.
Pero la geocerca sigue siendo lo único que dice a qué empresa pertenece cada
viaje, así que una que no dispara sigue siendo un problema: sus timbradas caen
en «Sin identificar».

Este comando no arregla nada, y lo que señala casi nunca se arregla en el
código: se arregla en la plataforma de Service24GPS, moviendo o creando la
geocerca. Sirve para saber si el que falla es un bus o una ruta entera.

Uso:
    python manage.py auditar_servicios
    python manage.py auditar_servicios --desde 2026-08-01 --hasta 2026-08-25
    python manage.py auditar_servicios --umbral 1.5 --csv auditoria.csv
"""

import csv
import re
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from tracking import services

# Un bus que llevó pasajeros ese día necesariamente hizo al menos un viaje, y
# ese viaje debería haber cruzado la geocerca de su empresa. Por debajo de este
# promedio de entradas por día activo la geocerca no está cubriendo la ruta.
UMBRAL_POR_DEFECTO = 1.0


class Command(BaseCommand):
    help = ('Lista las unidades a las que la geocerca les dispara menos veces '
            'que los días que trabajaron (o ninguna), y las que aun así '
            'quedan con una ocupación imposible.')

    def add_arguments(self, parser):
        parser.add_argument('--desde', help='YYYY-MM-DD. Por defecto, el último mes.')
        parser.add_argument('--hasta', help='YYYY-MM-DD. Por defecto, hoy.')
        parser.add_argument(
            '--umbral', type=float, default=UMBRAL_POR_DEFECTO,
            help=f'Entradas a geocerca por día activo por debajo del cual se '
                 f'marca la unidad (por defecto {UMBRAL_POR_DEFECTO}).')
        parser.add_argument('--csv', help='Escribe el resultado completo a un CSV.')
        parser.add_argument(
            '--todas', action='store_true',
            help='Muestra todas las unidades, no solo las sospechosas.')

    def handle(self, *args, **options):
        desde, hasta = options['desde'], options['hasta']
        if not (desde and hasta):
            por_defecto = services.rango_ultimo_mes()
            desde = desde or por_defecto[0]
            hasta = hasta or por_defecto[1]
        if desde > hasta:
            raise CommandError(f'El rango va al revés: {desde} > {hasta}')

        self.stdout.write(f'Consultando {desde} a {hasta}...')
        datos = services.range_summary(desde, hasta)

        filas = self._auditar(datos)
        sospechosas = [f for f in filas if f['diagnostico']]

        self._imprimir(filas if options['todas'] else sospechosas, desde, hasta)
        self._por_ruta(sospechosas)
        self._resumen(filas, sospechosas, options['umbral'])

        if options['csv']:
            self._escribir_csv(options['csv'], filas)
            self.stdout.write(self.style.SUCCESS(f"\nCSV escrito: {options['csv']}"))

    # -- cálculo ------------------------------------------------------------

    def _auditar(self, datos):
        """Una fila por unidad, con su diagnóstico.

        `días activos` sale del detalle diario: un día cuenta como activo si el
        bus registró al menos una timbrada. Es el piso duro de las entradas a
        geocerca que deberían haberse visto, porque para timbrar hay que haber
        hecho el viaje.
        """
        internos = datos['detalle']['internos']
        dias_activos = defaultdict(int)
        for fila in datos['detalle']['filas']:
            for interno, valor in zip(internos, fila['valores']):
                if valor:
                    dias_activos[interno] += 1

        rutas = self._rutas_por_interno()
        filas = []
        for v in datos['vehiculos']:
            interno = v['interno']
            activos = dias_activos.get(interno, 0)
            servicios = v['servicios']
            entradas = v['entradas_geocerca']
            timbradas = v['timbradas']
            por_dia = round(entradas / activos, 2) if activos else None
            por_servicio = round(timbradas / servicios, 1) if servicios else None

            diagnostico = []
            if timbradas and not entradas:
                diagnostico.append('SIN GEOCERCA')
            elif activos and entradas < activos:
                diagnostico.append(f'{entradas} entradas de geocerca en '
                                   f'{activos} días activos')
            if v['ocupacion'] is not None and v['ocupacion'] > 100:
                diagnostico.append(f"ocupación {v['ocupacion']}%")
            if (v['capacidad'] and por_servicio
                    and por_servicio > v['capacidad']):
                diagnostico.append(
                    f"{por_servicio} pasajeros/servicio > {v['capacidad']} asientos")

            filas.append({
                'interno': interno,
                'ruta': rutas.get(self._clave_flota(interno), ''),
                'capacidad': v['capacidad'],
                'timbradas': timbradas,
                'servicios': servicios,
                'entradas_geocerca': entradas,
                'dias_activos': activos,
                'entradas_por_dia': por_dia,
                'pasajeros_por_servicio': por_servicio,
                'ocupacion': v['ocupacion'],
                'diagnostico': '; '.join(diagnostico),
            })

        filas.sort(key=lambda f: (f['entradas_por_dia'] if f['entradas_por_dia'] is not None
                                  else 999, -f['timbradas']))
        return filas

    @staticmethod
    def _clave_flota(interno):
        """Número del interno, para cruzar con la planilla.

        Se toma el último grupo de dígitos y no todos: los internos vienen como
        'INT 7078' pero también como 'NIN673 INT 7306', donde el número que
        identifica al bus en la planilla es el segundo.
        """
        numeros = re.findall(r'\d+', interno or '')
        return numeros[-1] if numeros else ''

    def _rutas_por_interno(self):
        """Ruta asignada a cada interno, si la planilla ya está importada."""
        try:
            from tracking.models import FlotaVehiculo
            return {self._clave_flota(v.no_interno): (v.ruta or '').strip()
                    for v in FlotaVehiculo.objects.all()}
        except Exception:
            # La tabla puede no existir todavía; la ruta es informativa.
            return {}

    # -- salida -------------------------------------------------------------

    def _imprimir(self, filas, desde, hasta):
        self.stdout.write(f'\nAuditoría de servicios · {desde} a {hasta}\n')
        if not filas:
            self.stdout.write(self.style.SUCCESS(
                'Ninguna unidad sospechosa: a todas les dispara la geocerca '
                'al menos una vez por día activo.'))
            return
        cab = (f"{'Interno':<18}{'Ruta':<10}{'Cap':>4}{'Timbr':>7}{'Serv':>6}"
               f"{'Geoc':>6}{'Días':>6}{'G/día':>7}{'Pas/serv':>9}{'Ocup%':>8}"
               f"  Diagnóstico")
        self.stdout.write(cab)
        self.stdout.write('-' * len(cab))
        for f in filas:
            self.stdout.write(
                f"{f['interno']:<18}{f['ruta'][:9]:<10}"
                f"{self._n(f['capacidad']):>4}{f['timbradas']:>7}{f['servicios']:>6}"
                f"{f['entradas_geocerca']:>6}{f['dias_activos']:>6}"
                f"{self._n(f['entradas_por_dia']):>7}"
                f"{self._n(f['pasajeros_por_servicio']):>9}"
                f"{self._n(f['ocupacion']):>8}  "
                + self.style.WARNING(f['diagnostico']))

    def _por_ruta(self, sospechosas):
        """Agrupa por ruta: si cae una ruta entera, la geocerca es la culpable."""
        if not sospechosas:
            return
        por_ruta = defaultdict(list)
        for f in sospechosas:
            por_ruta[f['ruta'] or '(sin ruta en planilla)'].append(f['interno'])
        self.stdout.write('\nPor ruta (una ruta entera apunta a la geocerca, '
                          'no a los buses):')
        for ruta, internos in sorted(por_ruta.items(), key=lambda kv: -len(kv[1])):
            self.stdout.write(f'  {ruta:<24} {len(internos):>2} unidades: '
                              f"{', '.join(sorted(internos))}")

    def _resumen(self, filas, sospechosas, umbral):
        bajo_umbral = [f for f in filas
                       if f['entradas_por_dia'] is not None and f['entradas_por_dia'] < umbral]
        sin_geo = [f for f in filas
                   if f['timbradas'] and not f['entradas_geocerca']]
        self.stdout.write(
            f'\n{len(sospechosas)} de {len(filas)} unidades con la geocerca '
            f'mal cubierta.')
        self.stdout.write(
            f'{len(bajo_umbral)} por debajo de {umbral} entradas de geocerca '
            f'por día activo; {len(sin_geo)} sin ninguna pese a tener '
            f'timbradas.')

    def _escribir_csv(self, ruta, filas):
        campos = ['interno', 'ruta', 'capacidad', 'timbradas', 'servicios',
                  'entradas_geocerca', 'dias_activos', 'entradas_por_dia',
                  'pasajeros_por_servicio', 'ocupacion', 'diagnostico']
        with open(ruta, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=campos)
            w.writeheader()
            w.writerows(filas)

    @staticmethod
    def _n(valor):
        return '—' if valor is None else str(valor)
