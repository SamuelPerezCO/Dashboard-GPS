"""Pruebas del dashboard. El API va simulado: la suite no toca la red."""

import os
from datetime import date, datetime
from io import StringIO
from unittest.mock import patch

from config.settings import _catalogo_de
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection
from django.test.utils import CaptureQueriesContext
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import services, views
from .middleware import CLAVE_SESION, CLAVE_USUARIO
from .admin import DashboardUsuarioForm
from .models import DashboardUsuario

# Correos y catálogo fijos para las pruebas. El de settings sale del entorno
# (DASHBOARD_CORREO_* y DASHBOARD_CLAVE_*), y la suite no puede depender de lo
# que cada máquina tenga escrito en su .env.
ADMIN = 'admin@rastrelital.com'
PROCAPS = 'procaps@rastrelital.com'
DITAR = 'ditar@rastrelital.com'
RELIANZ = 'relianz@rastrelital.com'

CATALOGO = {
    ADMIN:   {'clave': 'Admin', 'empresas': None},
    PROCAPS: {'clave': 'Procaps', 'empresas': ('PROCAPS',)},
    DITAR:   {'clave': 'Ditar', 'empresas': ('DITAR',)},
    RELIANZ: {'clave': 'relianz', 'empresas': ('RELIANZ',)},
    # TEMPORAL: la cuenta del botón de acceso sin cuenta. No es un correo
    # porque a esa entrada no se llega por el formulario, sino por su vista.
    'invitado': {'clave': 'no-se-escribe-a-mano', 'empresas': None},
}

con_catalogo = override_settings(DASHBOARD_USUARIOS=CATALOGO)

# PBKDF2 tarda a propósito (~150 ms por clave) y eso se nota cuando la suite
# crea cuentas de prueba. Para lo que se comprueba aquí da igual el algoritmo:
# lo que importa es que la clave se guarde hasheada y que se verifique bien.
rapido_al_hashear = override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])


def _alerta(equipo, hora, geocerca, fecha='2026-07-20', fuera=False):
    """Arma una alerta como las que manda el API.

    Args:
        fuera: La vuelve una salida de geocerca en vez de una entrada.
    """
    sentido = 'FUERA DE LA' if fuera else 'DENTRO DE'
    return {
        'Equipo': equipo,
        'Fecha': fecha,
        'Hora': hora,
        'TipoAlerta': 'GEOCERCA',
        'StatusAlerta': 'FUERA' if fuera else 'DENTRO',
        'Descripcion': (f'Unidad WEO 371 Generó ALERTA {sentido} GEOCERCA '
                        f'{geocerca} el {fecha.replace("-", "/")} {hora}'),
    }


class NombreGeocercaTests(TestCase):
    """Sacar el nombre de la geocerca del texto de la alerta."""

    def test_extrae_el_nombre(self):
        self.assertEqual(
            services._nombre_geocerca(_alerta('1', '08:00:00', 'PROCAPS')),
            'PROCAPS')

    def test_deshace_entidades_html(self):
        alerta = _alerta('1', '08:00:00', 'RUTA 3 &amp; 4')
        self.assertEqual(services._nombre_geocerca(alerta), 'RUTA 3 & 4')

    def test_sin_patron_devuelve_vacio(self):
        self.assertEqual(services._nombre_geocerca({'Descripcion': 'otra cosa'}), '')
        self.assertEqual(services._nombre_geocerca({}), '')


class EmpresaDeGeocercaTests(TestCase):
    """A qué empresa pertenece cada nombre de geocerca."""

    def test_procaps_va_anclado_al_inicio(self):
        self.assertEqual(services.empresa_de_geocerca('PROCAPS'), 'PROCAPS')
        self.assertEqual(services.empresa_de_geocerca('procaps planta'), 'PROCAPS')
        self.assertEqual(services.empresa_de_geocerca('RUTA 12'), 'PROCAPS')
        self.assertIsNone(services.empresa_de_geocerca('MI PROCAPS'))

    def test_ditar_y_relianz_en_cualquier_parte(self):
        for nombre in ('DITAR', 'BODEGA DITAR', 'ditar planta 2'):
            self.assertEqual(services.empresa_de_geocerca(nombre), 'DITAR', nombre)
        for nombre in ('RELIANZ', 'CD RELIANZ NORTE', 'relianz'):
            self.assertEqual(services.empresa_de_geocerca(nombre), 'RELIANZ', nombre)

    def test_no_confunde_palabras_parecidas(self):
        self.assertIsNone(services.empresa_de_geocerca('DITARIO'))
        self.assertIsNone(services.empresa_de_geocerca('RELIANZA'))

    def test_desconocidas_y_vacios(self):
        for nombre in ('TALLER', '', None):
            self.assertIsNone(services.empresa_de_geocerca(nombre))


class TabDeEmpresaTests(TestCase):
    """En qué pestaña cae cada empresa."""

    def test_cada_empresa_a_su_pestana(self):
        for e in ('PROCAPS', 'DITAR', 'RELIANZ'):
            self.assertEqual(services.tab_de_empresa(e), e)

    def test_lo_desconocido_cae_en_sin_identificar(self):
        self.assertEqual(services.tab_de_empresa(None), services.TAB_SIN_IDENTIFICAR)
        self.assertEqual(services.tab_de_empresa('OTRA'), services.TAB_SIN_IDENTIFICAR)

    def test_todas_las_pestanas_tienen_etiqueta(self):
        self.assertEqual(sorted(services.ETIQUETA_EMPRESA), sorted(services.EMPRESAS))


class TabsPermitidasTests(TestCase):
    """Qué pestañas ve cada usuario."""

    def test_sin_restriccion_ve_todo(self):
        self.assertEqual(services.tabs_permitidas(None), list(services.EMPRESAS))

    def test_una_empresa_ve_la_suya_y_sin_identificar(self):
        self.assertEqual(services.tabs_permitidas(['PROCAPS']),
                         ['PROCAPS', services.TAB_SIN_IDENTIFICAR])
        self.assertEqual(services.tabs_permitidas(['DITAR']),
                         ['DITAR', services.TAB_SIN_IDENTIFICAR])

    def test_no_ve_las_de_los_demas(self):
        self.assertNotIn('DITAR', services.tabs_permitidas(['PROCAPS']))
        self.assertNotIn('RELIANZ', services.tabs_permitidas(['PROCAPS']))

    def test_respeta_el_orden_del_catalogo(self):
        self.assertEqual(services.tabs_permitidas(['RELIANZ', 'PROCAPS']),
                         ['PROCAPS', 'RELIANZ', services.TAB_SIN_IDENTIFICAR])

    def test_ignora_nombres_que_no_existen(self):
        self.assertEqual(services.tabs_permitidas(['INVENTADA']),
                         [services.TAB_SIN_IDENTIFICAR])


class EntradaGeocercaTests(TestCase):
    """Qué alertas cuentan como servicio y cuáles no."""

    def test_entrada_cuenta(self):
        self.assertTrue(services._es_entrada_geocerca(_alerta('1', '08:00:00', 'PROCAPS')))

    def test_salida_no_cuenta(self):
        salida = _alerta('1', '09:00:00', 'PROCAPS', fuera=True)
        self.assertFalse(services._es_entrada_geocerca(salida))

    def test_otra_alerta_no_cuenta(self):
        self.assertFalse(services._es_entrada_geocerca(
            {'TipoAlerta': 'EXCESO DE VELOCIDAD', 'StatusAlerta': '', 'Descripcion': ''}))


class EmpresaDeTimbradaTests(TestCase):
    """A qué servicio, y con él a qué empresa y ruta, se apunta cada timbrada."""

    SERVICIOS = [('08:00:00', 'PROCAPS', 'RUTA 1'),
                 ('14:00:00', 'DITAR', 'DITAR NORTE')]

    def test_toma_el_servicio_siguiente(self):
        self.assertEqual(
            services._servicio_de_timbrada('07:30:00', self.SERVICIOS),
            ('PROCAPS', 'RUTA 1'))
        self.assertEqual(
            services._servicio_de_timbrada('10:00:00', self.SERVICIOS),
            ('DITAR', 'DITAR NORTE'))

    def test_despues_del_ultimo_usa_el_ultimo(self):
        self.assertEqual(
            services._servicio_de_timbrada('23:00:00', self.SERVICIOS),
            ('DITAR', 'DITAR NORTE'))

    def test_sin_servicios_no_hay_empresa(self):
        self.assertEqual(services._servicio_de_timbrada('10:00:00', []), (None, ''))


class NormalizarRutaTests(TestCase):
    """Las rutas se comparan en mayúsculas y con un solo espacio."""

    def test_normaliza(self):
        self.assertEqual(services._norm_ruta(' ruta   1 '), 'RUTA 1')
        self.assertEqual(services._norm_ruta(None), '')


class NormalizarInternoTests(TestCase):
    """Los internos se comparan sin espacios y en mayúsculas."""

    def test_normaliza(self):
        self.assertEqual(services._norm_interno('INT 7074'), 'INT7074')
        self.assertEqual(services._norm_interno('  int  7074 '), 'INT7074')
        self.assertEqual(services._norm_interno(None), '')

    def test_la_capacidad_se_encuentra(self):
        self.assertEqual(
            services.CAPACIDAD_POR_INTERNO[services._norm_interno('INT 7076')], 30)

    def test_el_tipo_se_encuentra(self):
        self.assertEqual(
            services.TIPO_POR_INTERNO[services._norm_interno('INT 7076')], 'BUSETA')
        self.assertEqual(
            services.TIPO_POR_INTERNO[services._norm_interno('INT 7307')], 'BUSETON')
        self.assertEqual(
            services.TIPO_POR_INTERNO[services._norm_interno('INT 7273')], 'BUS')

    def test_los_internos_sin_tipo_conservan_su_capacidad(self):
        clave = services._norm_interno('INT 7239')
        self.assertEqual(services.CAPACIDAD_POR_INTERNO[clave], 40)
        self.assertNotIn(clave, services.TIPO_POR_INTERNO)


class FranjaDeHorasTests(TestCase):
    """Las franjas de horas, las de los turnos y las escritas a mano."""

    def test_la_franja_del_turno_llega_hasta_el_siguiente(self):
        self.assertEqual(services.franja_de_turno('MANANA'), (4 * 60, 12 * 60))
        self.assertEqual(services.franja_de_turno('TARDE'), (12 * 60, 18 * 60 + 30))
        self.assertEqual(services.franja_de_turno('NOCHE'), (18 * 60 + 30, 4 * 60))
        self.assertIsNone(services.franja_de_turno(None))

    def test_la_franja_normal_incluye_el_inicio_y_no_el_fin(self):
        franja = (8 * 60, 10 * 60)
        self.assertTrue(services.en_franja('08:00:00', franja))
        self.assertTrue(services.en_franja('09:59', franja))
        self.assertFalse(services.en_franja('10:00', franja))
        self.assertFalse(services.en_franja('07:59', franja))

    def test_la_franja_que_cruza_medianoche_se_parte_en_dos(self):
        franja = (22 * 60, 2 * 60)
        for hora in ('22:00', '23:59', '00:00', '01:59'):
            self.assertTrue(services.en_franja(hora, franja), hora)
        for hora in ('21:59', '02:00', '12:00'):
            self.assertFalse(services.en_franja(hora, franja), hora)

    def test_sin_franja_entra_todo_y_sin_hora_no_entra_nada(self):
        self.assertTrue(services.en_franja('cualquier cosa', None))
        self.assertFalse(services.en_franja('', (0, 60)))

    def test_la_etiqueta_muestra_la_ultima_hora_que_entra(self):
        self.assertEqual(services.etiqueta_de_franja((4 * 60, 12 * 60)),
                         'Horas 04:00 – 11:59')


class ContarServiciosTests(TestCase):
    """Un servicio es una tanda de timbradas separada de la siguiente."""

    @staticmethod
    def _t(*horas, fecha='2026-07-20'):
        return [{'fecha': fecha, 'hora': h} for h in horas]

    def test_sin_timbradas_no_hay_viajes(self):
        self.assertEqual(services._contar_servicios([]), 0)

    def test_las_timbradas_seguidas_son_un_solo_viaje(self):
        self.assertEqual(services._contar_servicios(
            self._t('04:10:00', '04:35:00', '04:55:00')), 1)

    def test_un_silencio_largo_abre_otro_viaje(self):
        self.assertEqual(services._contar_servicios(
            self._t('04:10:00', '05:05:00')), 2)

    def test_el_hueco_justo_todavia_es_el_mismo_viaje(self):
        limite = services.HUECO_ENTRE_SERVICIOS
        self.assertEqual(services._contar_servicios(
            self._t('04:00:00', f'04:{limite:02d}:00')), 1)
        self.assertEqual(services._contar_servicios(
            self._t('04:00:00', f'04:{limite + 1:02d}:00')), 2)

    def test_no_le_importa_en_que_orden_lleguen(self):
        self.assertEqual(services._contar_servicios(
            self._t('06:15:00', '04:10:00', '04:20:00')), 2)

    def test_cada_dia_cuenta_por_su_lado(self):
        self.assertEqual(services._contar_servicios(
            self._t('05:00:00') + self._t('05:00:00', fecha='2026-07-21')), 2)

    def test_las_timbradas_sin_hora_no_cuentan(self):
        self.assertEqual(services._contar_servicios(self._t('', 'a deshoras')), 0)


@patch.object(services.api_client, 'get_alerts')
@patch.object(services.api_client, 'get_vehicles')
@patch.object(services.api_client, 'get_passenger_events')
class RangeSummaryTests(TestCase):
    """El resumen del rango: conteos, ocupación y filtros por empresa."""

    VEHICULOS = [
        {'idgps': '100', 'nombre': 'INT 7076'},
        {'idgps': '200', 'nombre': 'INT 9999'},
    ]

    def setUp(self):
        cache.clear()

    def test_cuenta_servicios_timbradas_y_ocupacion(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS
        alertas.return_value = [_alerta('100', '08:00:00', 'PROCAPS'),
                                _alerta('100', '14:00:00', 'PROCAPS')]
        # Dos tandas de timbradas, o sea dos viajes: 15 pasajeros suben
        # alrededor de las 08:30 y 16 alrededor de las 14:30.
        eventos.side_effect = lambda equipo, *a, **k: (
            [{'fecha': '2026-07-20', 'hora': '08:30:00', 'pasajero': 'a'}] * 15
            + [{'fecha': '2026-07-20', 'hora': '14:30:00', 'pasajero': 'b'}] * 16
            if equipo == '100' else [])

        r = services.range_summary('2026-07-20', '2026-07-20')

        por_interno = {v['interno']: v for v in r['vehiculos']}
        self.assertEqual(por_interno['INT 7076']['servicios'], 2)
        self.assertEqual(por_interno['INT 7076']['entradas_geocerca'], 2)
        self.assertEqual(por_interno['INT 7076']['timbradas'], 31)
        self.assertEqual(por_interno['INT 7076']['capacidad'], 30)
        self.assertEqual(por_interno['INT 7076']['ocupacion'], 51.67)
        self.assertEqual(por_interno['INT 7076']['capacidad_total'], 60)
        self.assertIsNone(por_interno['INT 9999']['ocupacion'])
        self.assertEqual(r['unidades_con_error'], 0)

    def test_filtra_por_empresa(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '08:00:00', 'PROCAPS'),
                                _alerta('100', '14:00:00', 'DITAR')]
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '07:00:00', 'pasajero': 'a'},
            {'fecha': '2026-07-20', 'hora': '13:00:00', 'pasajero': 'b'},
        ]

        procaps = services.range_summary('2026-07-20', '2026-07-20', 'PROCAPS')
        ditar = services.range_summary('2026-07-20', '2026-07-20', 'DITAR')

        self.assertEqual(procaps['vehiculos'][0]['servicios'], 1)
        self.assertEqual(procaps['vehiculos'][0]['timbradas'], 1)
        self.assertEqual(ditar['vehiculos'][0]['servicios'], 1)
        self.assertEqual(ditar['vehiculos'][0]['timbradas'], 1)

    def test_la_ocupacion_lleva_dos_decimales(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '08:00:00', 'PROCAPS'),
                                _alerta('100', '14:00:00', 'PROCAPS')]
        eventos.side_effect = lambda equipo, *a, **k: (
            [{'fecha': '2026-07-20', 'hora': '08:30:00', 'pasajero': 'a'}] * 29
            + [{'fecha': '2026-07-20', 'hora': '14:30:00', 'pasajero': 'b'}] * 29)

        r = services.range_summary('2026-07-20', '2026-07-20')

        self.assertEqual(r['vehiculos'][0]['ocupacion'], 96.67)
        self.assertEqual(r['ocupacion_flota'], 96.67)

    def test_filtra_por_tipo_de_vehiculo(self, eventos, vehiculos, alertas):
        # 7076 es buseta y 7273 es bus; 9999 no está en la planilla.
        vehiculos.return_value = [{'idgps': '100', 'nombre': 'INT 7076'},
                                  {'idgps': '200', 'nombre': 'INT 7273'},
                                  {'idgps': '300', 'nombre': 'INT 9999'}]
        alertas.return_value = []
        eventos.side_effect = lambda equipo, *a, **k: []

        buseta = services.range_summary('2026-07-20', '2026-07-20', tipo='BUSETA')
        bus = services.range_summary('2026-07-20', '2026-07-20', tipo='BUS')
        todos = services.range_summary('2026-07-20', '2026-07-20')

        self.assertEqual([v['interno'] for v in buseta['vehiculos']], ['INT 7076'])
        self.assertEqual([v['interno'] for v in bus['vehiculos']], ['INT 7273'])
        self.assertEqual(len(todos['vehiculos']), 3)
        self.assertEqual(buseta['tipo'], 'BUSETA')
        self.assertEqual(buseta['vehiculos'][0]['tipo'], 'BUSETA')
        self.assertIsNone(todos['tipo'])

    def test_todas_respeta_el_techo_del_usuario(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '08:00:00', 'PROCAPS'),
                                _alerta('100', '14:00:00', 'DITAR')]
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '07:00:00', 'pasajero': 'a'},
            {'fecha': '2026-07-20', 'hora': '13:00:00', 'pasajero': 'b'},
        ]

        r = services.range_summary('2026-07-20', '2026-07-20', None,
                                   services.tabs_permitidas(['PROCAPS']))

        self.assertEqual(r['vehiculos'][0]['servicios'], 1)
        self.assertEqual(r['vehiculos'][0]['timbradas'], 1)
        self.assertEqual(r['empresas'], ['PROCAPS', services.TAB_SIN_IDENTIFICAR])
        self.assertNotIn('DITAR', r['etiquetas'])

    def test_lo_sin_identificar_va_en_todas_las_ventanas(self, eventos,
                                                        vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = []
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '09:00:00', 'pasajero': 'a'}]

        for empresa in ('PROCAPS', 'DITAR', 'RELIANZ'):
            r = services.range_summary('2026-07-20', '2026-07-20', None,
                                       services.tabs_permitidas([empresa]))
            self.assertEqual(r['vehiculos'][0]['timbradas'], 1, empresa)

    def test_las_timbradas_sin_servicio_caen_en_sin_identificar(self, eventos,
                                                                vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = []
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '09:00:00', 'pasajero': 'a'}]

        todas = services.range_summary('2026-07-20', '2026-07-20')
        sin_id = services.range_summary('2026-07-20', '2026-07-20',
                                        services.TAB_SIN_IDENTIFICAR)

        self.assertEqual(todas['timbradas_inferidas'], 1)
        self.assertEqual(sin_id['vehiculos'][0]['timbradas'], 1)

    def test_un_fallo_del_api_se_reporta_y_no_tumba_el_dashboard(self, eventos,
                                                                 vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS
        alertas.return_value = []

        def falla_el_200(equipo, *a, **k):
            if equipo == '200':
                raise services.api_client.ApiError('boom')
            return [{'fecha': '2026-07-20', 'hora': '09:00:00', 'pasajero': 'a'}]

        eventos.side_effect = falla_el_200
        with self.assertLogs('tracking.services', level='ERROR'):
            r = services.range_summary('2026-07-20', '2026-07-20')

        self.assertEqual(r['unidades_con_error'], 1)
        self.assertEqual(len(r['vehiculos']), 2)

    def test_invierte_el_rango_al_reves(self, eventos, vehiculos, alertas):
        vehiculos.return_value = []
        alertas.return_value = []
        eventos.return_value = []
        r = services.range_summary('2026-07-25', '2026-07-20')
        self.assertEqual((r['desde'], r['hasta']), ('2026-07-20', '2026-07-25'))

    def test_la_franja_a_mano_recorta_servicios_y_timbradas(
            self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '08:00:00', 'PROCAPS'),
                                _alerta('100', '14:00:00', 'PROCAPS')]
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '08:30:00', 'pasajero': 'a'},
            {'fecha': '2026-07-20', 'hora': '14:30:00', 'pasajero': 'b'},
        ]

        r = services.range_summary('2026-07-20', '2026-07-20',
                                   franja=(8 * 60, 9 * 60))

        bus = r['vehiculos'][0]
        self.assertEqual((bus['servicios'], bus['timbradas']), (1, 1))
        self.assertIsNone(r['turno'])
        self.assertEqual(r['franja'], [480, 540])
        self.assertEqual(r['etiqueta_franja'], 'Horas 08:00 – 08:59')

    def test_la_franja_a_mano_le_gana_al_turno(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '14:00:00', 'PROCAPS')]
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '14:30:00', 'pasajero': 'a'}]

        r = services.range_summary('2026-07-20', '2026-07-20', turno='MANANA',
                                   franja=(12 * 60, 18 * 60))

        self.assertEqual(r['vehiculos'][0]['timbradas'], 1)
        self.assertIsNone(r['turno'])

    def test_lista_las_rutas_del_rango(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '08:00:00', 'RUTA 2'),
                                _alerta('100', '14:00:00', 'RUTA 1'),
                                _alerta('100', '16:00:00', 'RUTA 1')]
        eventos.return_value = []

        r = services.range_summary('2026-07-20', '2026-07-20')

        self.assertEqual(r['rutas'], ['RUTA 1', 'RUTA 2'])
        self.assertIsNone(r['ruta'])

    def test_filtra_por_ruta(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '08:00:00', 'RUTA 1'),
                                _alerta('100', '14:00:00', 'RUTA 2')]
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '07:00:00', 'pasajero': 'a'},
            {'fecha': '2026-07-20', 'hora': '13:00:00', 'pasajero': 'b'},
        ]

        r = services.range_summary('2026-07-20', '2026-07-20', ruta='ruta 1')

        bus = r['vehiculos'][0]
        self.assertEqual((bus['servicios'], bus['timbradas']), (1, 1))
        self.assertEqual(r['ruta'], 'RUTA 1')
        # El selector no se recorta con lo elegido: siguen las dos rutas.
        self.assertEqual(r['rutas'], ['RUTA 1', 'RUTA 2'])

    def test_una_ruta_que_no_existe_deja_todo_en_cero(self, eventos, vehiculos,
                                                      alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '08:00:00', 'RUTA 1')]
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '07:00:00', 'pasajero': 'a'}]

        r = services.range_summary('2026-07-20', '2026-07-20', ruta='RUTA 9')

        bus = r['vehiculos'][0]
        self.assertEqual((bus['servicios'], bus['timbradas']), (0, 0))

    def test_un_servicio_es_una_tanda_de_timbradas(self, eventos, vehiculos,
                                                   alertas):
        """Las timbradas seguidas son un viaje; un silencio largo abre otro."""
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = []
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': h, 'pasajero': 'a'} for h in (
                # Un viaje: la gente va subiendo con huecos de 25 y 20 minutos.
                '04:10:00', '04:35:00', '04:55:00',
                # Otro: el bus estuvo quieto 75 minutos.
                '06:10:00', '06:20:00',
            )]

        r = services.range_summary('2026-07-20', '2026-07-20')

        self.assertEqual(r['vehiculos'][0]['servicios'], 2)

    def test_los_viajes_sin_geocerca_tambien_cuentan(self, eventos, vehiculos,
                                                     alertas):
        """El caso que inflaba la ocupación al doble.

        El bus recoge en las casas y deja en la planta (eso sí entra a
        PROCAPS), y enseguida carga a los que salen de turno y los lleva a su
        casa: ese segundo viaje no entra a ninguna geocerca porque termina en
        las casas de la gente. Contando entradas eran 30 pasajeros en un solo
        servicio; contando tandas son dos viajes, que es lo que pasó.
        """
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '05:40:00', 'PROCAPS')]
        eventos.side_effect = lambda equipo, *a, **k: (
            [{'fecha': '2026-07-20', 'hora': '04:30:00', 'pasajero': 'a'}] * 15
            + [{'fecha': '2026-07-20', 'hora': '06:15:00', 'pasajero': 'b'}] * 15)

        r = services.range_summary('2026-07-20', '2026-07-20')

        bus = r['vehiculos'][0]
        self.assertEqual(bus['entradas_geocerca'], 1)
        self.assertEqual((bus['servicios'], bus['timbradas']), (2, 30))
        self.assertEqual(bus['ocupacion'], 50.0)

    def test_los_viajes_se_cuentan_por_dia(self, eventos, vehiculos, alertas):
        """Una tanda en cada día son dos viajes, no uno partido en dos."""
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = []
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '05:00:00', 'pasajero': 'a'},
            {'fecha': '2026-07-21', 'hora': '05:00:00', 'pasajero': 'b'},
        ]

        r = services.range_summary('2026-07-20', '2026-07-21')

        self.assertEqual(r['vehiculos'][0]['servicios'], 2)

    def test_el_detalle_trae_una_fila_por_dia(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = []
        eventos.return_value = []
        r = services.range_summary('2026-07-20', '2026-07-22')
        self.assertEqual([f['fecha'] for f in r['detalle']['filas']],
                         ['2026-07-20', '2026-07-21', '2026-07-22'])


@con_catalogo
class SinBaseDeDatosTests(TestCase):
    """Lo que sigue sin depender de la base, ahora que las cuentas sí.

    Las cuentas viven en DashboardUsuario desde que se administran por
    /admin/, así que entrar la consulta: el candado de «cero consultas» que
    había aquí dejó de describir el diseño. Lo que sí se sostiene es que la
    sesión va en una cookie firmada —no hay tabla de sesiones que
    escribir— y que una base caída no deja a nadie fuera, porque el acceso
    de emergencia de settings sigue abriendo.
    """

    def test_la_sesion_va_en_cookie_firmada(self):
        self.assertEqual(settings.SESSION_ENGINE,
                         'django.contrib.sessions.backends.signed_cookies')

    def test_entrar_y_ver_el_dashboard_no_escriben_ninguna_fila(self):
        """Consultar las cuentas sí; escribir, nada."""
        with CaptureQueriesContext(connection) as consultas:
            r = self.client.post(reverse('tracking:login'),
                                 {'correo': ADMIN, 'clave': 'Admin'})
            self.client.get(reverse('tracking:dashboard'))
        self.assertEqual(r.status_code, 302)
        escrituras = [c['sql'] for c in consultas.captured_queries
                      if not c['sql'].lstrip().upper().startswith('SELECT')]
        self.assertEqual(escrituras, [])

    def test_una_base_caida_no_cierra_el_acceso_de_emergencia(self):
        """Para esto existe la cuenta de settings.

        El Postgres es remoto y a veces no contesta. Si esa consulta dejara
        escapar la excepción, una caída de la base sacaría del sitio a todo
        el mundo en vez de solo impedir las cuentas nuevas.
        """
        with patch.object(DashboardUsuario.objects, 'filter',
                          side_effect=OperationalError('sin conexión')):
            with self.assertLogs('tracking.middleware', level='ERROR'):
                r = self.client.post(reverse('tracking:login'),
                                     {'correo': ADMIN, 'clave': 'Admin'})
                self.assertEqual(r.status_code, 302)
                self.assertEqual(
                    self.client.get(reverse('tracking:dashboard')).status_code,
                    200)


@con_catalogo
class PrecalentamientoTests(TestCase):
    """El precalentamiento que lanza el login."""

    def setUp(self):
        cache.clear()
        self.login_url = reverse('tracking:login')

    def test_el_rango_es_el_ultimo_mes(self):
        desde, hasta = services.rango_ultimo_mes()
        d1 = datetime.strptime(desde, '%Y-%m-%d').date()
        d2 = datetime.strptime(hasta, '%Y-%m-%d').date()
        self.assertEqual(d2, date.today())
        self.assertEqual((d2 - d1).days, services.DIAS_ULTIMO_MES)

    def test_precalienta_exactamente_ese_rango(self):
        with patch.object(services, 'range_summary') as resumen:
            services.precalentar_ultimo_mes()
        resumen.assert_called_once_with(*services.rango_ultimo_mes())

    @patch.object(views, 'threading')
    def test_el_get_del_login_lo_lanza_una_sola_vez(self, hilos):
        self.client.get(self.login_url)
        hilos.Thread.assert_called_once_with(
            target=views._precalentar_dashboard,
            name='precalentar-dashboard', daemon=True)
        hilos.Thread.return_value.start.assert_called_once()
        self.client.get(self.login_url)
        hilos.Thread.assert_called_once()

    @patch.object(views, 'threading')
    def test_el_post_no_lo_lanza(self, hilos):
        self.client.post(self.login_url, {'correo': 'x@rastrelital.com', 'clave': 'y'})
        hilos.Thread.assert_not_called()

    @patch.object(views, 'threading')
    def test_con_sesion_iniciada_no_precalienta(self, hilos):
        self.client.post(self.login_url, {'correo': ADMIN, 'clave': 'Admin'})
        self.client.get(self.login_url)
        hilos.Thread.assert_not_called()

    def test_un_fallo_no_se_escapa_del_hilo(self):
        with patch.object(views.services, 'precalentar_ultimo_mes',
                          side_effect=RuntimeError('boom')):
            with self.assertLogs('tracking.views', level='ERROR'):
                views._precalentar_dashboard()

    def test_sin_credenciales_solo_se_anota(self):
        with patch.object(views.services, 'precalentar_ultimo_mes',
                          side_effect=views.api_client.ApiConfigError('faltan')):
            with self.assertLogs('tracking.views', level='INFO') as log:
                views._precalentar_dashboard()
        self.assertIn('omitido', log.output[0])


@con_catalogo
class LoginTests(TestCase):
    """Quién entra, quién no y cómo se frena a quien insiste."""

    def setUp(self):
        cache.clear()
        self.login_url = reverse('tracking:login')
        parche = patch.object(views, '_lanzar_precalentamiento',
                              return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def test_sin_sesion_todo_redirige_al_login(self):
        for nombre in ('tracking:dashboard', 'tracking:fleet',
                       'tracking:api_dashboard', 'tracking:api_fleet'):
            url = reverse(nombre)
            r = self.client.get(url)
            self.assertRedirects(r, f'{self.login_url}?next={url}',
                                 fetch_redirect_response=False)

    def test_la_portada_se_ve_sin_sesion(self):
        r = self.client.get(reverse('tracking:home'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Nuestras Soluciones')
        self.assertContains(r, f'href="{self.login_url}"')
        self.assertContains(r, 'ENTRAR')

    def test_el_login_es_la_raiz(self):
        self.assertEqual(self.login_url, '/')

    def test_el_login_se_ve_sin_sesion(self):
        r = self.client.get(self.login_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Rastrelital')
        self.assertNotContains(r, 'Expreso Brasilia')
        self.assertNotContains(r, '>Salir<')

    def test_el_login_lleva_a_la_portada(self):
        r = self.client.get(self.login_url)
        self.assertContains(r, f'href="{reverse("tracking:home")}"')
        self.assertContains(r, 'Ver la página web')

    def test_la_url_vieja_del_login_sigue_sirviendo(self):
        r = self.client.get(reverse('tracking:login_antiguo'))
        self.assertRedirects(r, self.login_url, fetch_redirect_response=False)

    def test_la_url_vieja_del_login_conserva_el_next(self):
        destino = reverse('tracking:fleet')
        r = self.client.get(reverse('tracking:login_antiguo'), {'next': destino})
        self.assertRedirects(r, f'{self.login_url}?next={destino}',
                             fetch_redirect_response=False)

    def test_credenciales_correctas(self):
        r = self.client.post(self.login_url, {'correo': ADMIN, 'clave': 'Admin'})
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)
        self.assertTrue(self.client.session.get(CLAVE_SESION))

    def test_credenciales_incorrectas(self):
        r = self.client.post(self.login_url, {'correo': ADMIN, 'clave': 'mala'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'incorrectos')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_respeta_next(self):
        destino = reverse('tracking:fleet')
        r = self.client.post(self.login_url,
                             {'correo': ADMIN, 'clave': 'Admin', 'next': destino})
        self.assertRedirects(r, destino, fetch_redirect_response=False)

    def test_no_sirve_de_trampolin_a_otro_sitio(self):
        r = self.client.post(self.login_url, {
            'correo': ADMIN, 'clave': 'Admin',
            'next': 'https://sitio-malo.example/x'})
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)

    def test_salir_cierra_la_sesion_solo_por_post(self):
        self.client.post(self.login_url, {'correo': ADMIN, 'clave': 'Admin'})
        salir = reverse('tracking:logout')

        self.client.get(salir)
        self.assertTrue(self.client.session.get(CLAVE_SESION))

        self.client.post(salir)
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_el_acceso_temporal_entra_como_invitado(self):
        """El botón de entrar sin cuenta deja la sesión lista, no revienta.

        Vale la pena el candado porque la vista pone la marca de «login
        recién hecho» justo después de `cycle_key()`, con la sesión en
        blanco: si en vez de escribirla alguien la lee, es un KeyError y un
        500 en la cara de quien pulsa el botón.
        """
        r = self.client.post(reverse('tracking:acceso_temporal'), {})

        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)
        self.assertTrue(self.client.session.get(CLAVE_SESION))
        self.assertEqual(self.client.session.get(CLAVE_USUARIO), 'invitado')
        self.assertTrue(self.client.session.get(views.CLAVE_LOGIN_NUEVO))

    def test_el_acceso_temporal_no_sirve_por_get(self):
        r = self.client.get(reverse('tracking:acceso_temporal'))
        self.assertRedirects(r, self.login_url, fetch_redirect_response=False)
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_freno_tras_varios_intentos_fallidos(self):
        for _ in range(views.MAX_INTENTOS):
            self.client.post(self.login_url, {'correo': 'x@rastrelital.com', 'clave': 'y'})
        r = self.client.post(self.login_url, {'correo': ADMIN, 'clave': 'Admin'})
        self.assertContains(r, 'Demasiados intentos')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_el_formulario_pide_un_correo(self):
        r = self.client.get(self.login_url)
        self.assertContains(r, 'name="correo"')
        self.assertContains(r, 'type="email"')
        self.assertNotContains(r, 'name="usuario"')

    def test_el_nombre_corto_de_antes_ya_no_entra(self):
        """Candado del cambio a correos: 'admin' era una cuenta válida.

        Quien lo escriba se lleva una explicación en vez del error genérico:
        decir que ahora va el correo habla del formato del campo, no de si
        esa cuenta existe, así que no delata a nadie.
        """
        r = self.client.post(self.login_url, {'usuario': 'admin', 'clave': 'Admin'})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.client.session.get(CLAVE_SESION))

        r = self.client.post(self.login_url, {'correo': 'admin', 'clave': 'Admin'})
        self.assertContains(r, 'el correo completo')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_el_nombre_corto_tambien_gasta_intentos(self):
        """La explicación del correo no es una puerta de atrás al freno."""
        for _ in range(views.MAX_INTENTOS):
            self.client.post(self.login_url, {'correo': 'admin', 'clave': 'Admin'})
        r = self.client.post(self.login_url, {'correo': ADMIN, 'clave': 'Admin'})
        self.assertContains(r, 'Demasiados intentos')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_una_clave_vacia_no_autentica(self):
        """Defensa en profundidad, por si el catálogo llega mal armado.

        `constant_time_compare('', '')` es cierto, así que una cuenta con la
        clave en blanco dejaría entrar sin escribir nada. Settings ya no deja
        armar una así, pero la vista tampoco tiene por qué confiar.
        """
        with self.settings(DASHBOARD_USUARIOS={ADMIN: {'clave': '',
                                                       'empresas': None}}):
            r = self.client.post(self.login_url, {'correo': ADMIN, 'clave': ''})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_el_admin_conserva_su_propio_login(self):
        # Ahora el login vive en '/', así que "no contiene el login" sería
        # cierto para cualquier ruta: lo que se comprueba es que el admin
        # redirige al suyo.
        r = self.client.get('/admin/')
        self.assertTrue(r.headers.get('Location', '').startswith('/admin/login'))


@con_catalogo
class AccesoPorEmpresaTests(TestCase):
    """Cada cuenta ve solo los viajes de su empresa, también en el JSON."""

    def setUp(self):
        cache.clear()
        self.login_url = reverse('tracking:login')
        parche = patch.object(views, '_lanzar_precalentamiento',
                              return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def entrar(self, correo, clave):
        return self.client.post(self.login_url, {'correo': correo, 'clave': clave})

    def test_las_cuatro_cuentas_entran_con_su_correo(self):
        for correo, clave in ((ADMIN, 'Admin'), (PROCAPS, 'Procaps'),
                              (DITAR, 'Ditar'), (RELIANZ, 'relianz')):
            self.client.post(reverse('tracking:logout'))
            cache.clear()
            self.entrar(correo, clave)
            self.assertEqual(self.client.session.get(CLAVE_USUARIO), correo)

    def test_el_correo_no_distingue_mayusculas_ni_espacios_pero_la_clave_si(self):
        self.entrar('  PROCAPS@Rastrelital.COM  ', 'Procaps')
        self.assertEqual(self.client.session.get(CLAVE_USUARIO), PROCAPS)

        self.client.post(reverse('tracking:logout'))
        self.entrar(PROCAPS, 'procaps')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_la_barra_muestra_el_nombre_corto_y_el_correo_entero(self):
        """El correo entero no cabe en la píldora de la cabecera.

        Ahí va lo que está antes del '@'; el correo completo se queda en el
        `title`, que es donde sí hay sitio para leerlo.
        """
        self.entrar(PROCAPS, 'Procaps')
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertEqual(r.context['usuario'], 'procaps')
        self.assertEqual(r.context['correo'], PROCAPS)
        self.assertContains(r, f'title="Sesión iniciada como {PROCAPS}"')

    def test_cada_cuenta_dibuja_solo_sus_pestanas(self):
        casos = {
            PROCAPS: ('Procaps', 'PROCAPS', ('DITAR', 'RELIANZ')),
            DITAR:   ('Ditar',   'DITAR',   ('PROCAPS', 'RELIANZ')),
            RELIANZ: ('relianz', 'RELIANZ', ('PROCAPS', 'DITAR')),
        }
        for correo, (clave, propia, ajenas) in casos.items():
            self.client.post(reverse('tracking:logout'))
            cache.clear()
            self.entrar(correo, clave)
            r = self.client.get(reverse('tracking:dashboard'))
            self.assertContains(r, f'data-empresa="{propia}"')
            self.assertContains(r, f'data-empresa="{services.TAB_SIN_IDENTIFICAR}"')
            for ajena in ajenas:
                self.assertNotContains(r, f'data-empresa="{ajena}"')

    def test_el_admin_dibuja_todas_las_pestanas(self):
        self.entrar(ADMIN, 'Admin')
        r = self.client.get(reverse('tracking:dashboard'))
        for valor in services.EMPRESAS:
            self.assertContains(r, f'data-empresa="{valor}"')

    def test_el_json_rechaza_la_empresa_ajena(self):
        self.entrar(PROCAPS, 'Procaps')
        r = self.client.get(reverse('tracking:api_dashboard'), {'empresa': 'DITAR'})
        self.assertEqual(r.status_code, 403)
        self.assertIn('no tiene acceso', r.json()['error'])

    def test_el_json_acepta_la_empresa_propia_y_la_sin_identificar(self):
        self.entrar(PROCAPS, 'Procaps')
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            for empresa in ('PROCAPS', services.TAB_SIN_IDENTIFICAR):
                r = self.client.get(reverse('tracking:api_dashboard'),
                                    {'empresa': empresa})
                self.assertEqual(r.status_code, 200, empresa)
        self.assertEqual(resumen.call_count, 2)

    def test_el_json_le_pasa_el_techo_del_usuario_a_services(self):
        self.entrar(DITAR, 'Ditar')
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            self.client.get(reverse('tracking:api_dashboard'),
                            {'desde': '2026-07-20', 'hasta': '2026-07-20'})
        resumen.assert_called_once_with(
            '2026-07-20', '2026-07-20', None,
            ['DITAR', services.TAB_SIN_IDENTIFICAR], None, None, None, None)

    def test_el_mapa_de_flota_es_solo_del_admin(self):
        self.entrar(RELIANZ, 'relianz')
        r = self.client.get(reverse('tracking:fleet'))
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)
        r = self.client.get(reverse('tracking:api_fleet'))
        self.assertEqual(r.status_code, 403)

    def test_el_admin_si_ve_el_mapa(self):
        self.entrar(ADMIN, 'Admin')
        self.assertEqual(self.client.get(reverse('tracking:fleet')).status_code, 200)

    def test_quitar_un_usuario_del_catalogo_cierra_su_sesion(self):
        self.entrar(PROCAPS, 'Procaps')
        catalogo = {k: v for k, v in settings.DASHBOARD_USUARIOS.items()
                    if k != PROCAPS}
        with self.settings(DASHBOARD_USUARIOS=catalogo):
            r = self.client.get(reverse('tracking:dashboard'))
        self.assertEqual(r.status_code, 302)
        self.assertIn(self.login_url, r.headers['Location'])


@con_catalogo
class PestanaTests(TestCase):
    """Cerrar la pestaña obliga a escribir correo y contraseña otra vez."""

    def setUp(self):
        cache.clear()
        self.login_url = reverse('tracking:login')
        parche = patch.object(views, '_lanzar_precalentamiento',
                              return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def entrar(self):
        self.client.post(self.login_url, {'correo': ADMIN, 'clave': 'Admin'})

    def test_la_pagina_de_entrada_sella_la_pestana(self):
        self.entrar()
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertTrue(r.context['sesion_nueva'])
        self.assertContains(r, 'rastrelital_pestana')

    def test_el_sello_es_de_un_solo_uso(self):
        self.entrar()
        self.client.get(reverse('tracking:dashboard'))
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertFalse(r.context['sesion_nueva'])

    def test_el_mapa_tambien_sella_al_entrar_directo(self):
        destino = reverse('tracking:fleet')
        self.client.post(self.login_url,
                         {'correo': ADMIN, 'clave': 'Admin', 'next': destino})
        r = self.client.get(destino)
        self.assertTrue(r.context['sesion_nueva'])

    def test_el_login_no_lleva_guardia(self):
        r = self.client.get(self.login_url)
        self.assertNotContains(r, 'rastrelital_pestana')

    def test_el_cierre_de_la_guardia_pasa_el_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.get(self.login_url)
        cliente.post(self.login_url, {
            'correo': ADMIN, 'clave': 'Admin',
            'csrfmiddlewaretoken': cliente.cookies['csrftoken'].value})
        self.assertTrue(cliente.session.get(CLAVE_SESION))

        r = cliente.post(reverse('tracking:logout'),
                         HTTP_X_CSRFTOKEN=cliente.cookies['csrftoken'].value)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(cliente.session.get(CLAVE_SESION))

    def test_la_cookie_muere_al_cerrar_el_navegador(self):
        self.entrar()
        cookie = self.client.cookies['sessionid']
        self.assertEqual(cookie['max-age'], '')
        self.assertEqual(cookie['expires'], '')


@con_catalogo
class DashboardTests(TestCase):
    """La página del dashboard y los errores de su endpoint JSON."""

    def setUp(self):
        cache.clear()
        self.client.post(reverse('tracking:login'),
                         {'correo': ADMIN, 'clave': 'Admin'})

    def test_dibuja_una_pestana_por_empresa(self):
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertEqual(r.status_code, 200)
        for valor in services.EMPRESAS:
            self.assertContains(r, f'data-empresa="{valor}"')
        self.assertContains(r, 'Sin identificar')

    def test_rechaza_fechas_invalidas(self):
        r = self.client.get(reverse('tracking:api_dashboard'), {'desde': '20-07-2026'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Fecha inválida', r.json()['error'])

    def test_rechaza_empresa_desconocida(self):
        r = self.client.get(reverse('tracking:api_dashboard'), {'empresa': 'INVENTADA'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('RELIANZ', r.json()['error'])

    def test_dibuja_los_selects_de_rango_y_de_turno(self):
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertContains(r, 'id="sel-rango"')
        self.assertContains(r, 'id="sel-turno"')
        self.assertContains(r, 'id="hora-desde"')
        # El rango solo ofrece los atajos y «Personalizado»: los días a mano
        # se eligen en «Desde» y «hasta».
        self.assertContains(r, 'Personalizado')
        self.assertNotContains(r, 'id="dias-custom"')
        for valor in services.TURNOS:
            self.assertContains(r, f'data-turno="{valor}"')

    def test_dibuja_el_select_de_tipo_de_vehiculo(self):
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertContains(r, 'id="sel-tipo"')
        for valor in services.TIPOS:
            self.assertContains(r, f'data-tipo="{valor}"')

    def test_dibuja_el_select_de_ruta(self):
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertContains(r, 'id="sel-ruta"')

    def test_marca_la_ocupacion_imposible_como_sospechosa(self):
        """Candado de regresión: INT 7078 (291,89%, bug real de 2026-08) no
        debe volver a dibujarse como una cifra confiable.

        Una ocupación por encima de 100% dice que faltaron alertas de
        geocerca, no que el bus fue sobrecupado (ver INFORME_ocupacion.md).
        La gráfica no debe dejar que ese valor fije la escala del eje
        —aplastaría el resto de las barras—, y tiene que marcarlo aparte en
        vez de dibujarlo como un dato más. Como todo esto vive en el
        JavaScript embebido de la plantilla (no hay runner de JS en el
        proyecto), el candado verifica que el HTML servido siga trayendo el
        umbral, el aviso y la clase que lo dibujan distinto.
        """
        r = self.client.get(reverse('tracking:dashboard'))
        html = r.content.decode()
        # El umbral de lo imposible, y que de verdad se use para filtrar la
        # escala del eje y no solo para decorar.
        self.assertIn('UMBRAL_OCUPACION_IMPOSIBLE = 100', html)
        self.assertIn('avisos[i] != null', html)
        # La barra sospechosa se distingue con su propia clase, no con el
        # mismo azul de siempre.
        self.assertIn('bar-sospechoso', html)
        self.assertIn("clase = sospechoso ? 'bar bar-sospechoso' : 'bar'", html)
        # El valor real (291,89%) se sigue mostrando: no se oculta ni se
        # recorta a 100.
        self.assertIn('fmtDec(values[i], decimales)', html)
        self.assertNotIn('Math.min(v, 100)', html)
        # La etiqueta del eje ya no se recorta («91,89%» en vez de
        # «291,89%»): el margen izquierdo se ensanchó de 38 a 52.
        self.assertIn('const pl = 52, pr = 12, pt = 10, pb = 74;', html)

    def test_la_ruta_llega_a_services(self):
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            r = self.client.get(reverse('tracking:api_dashboard'), {'ruta': ' RUTA 3 '})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(resumen.call_args.args[7], 'RUTA 3')

    def test_sin_ruta_no_se_filtra(self):
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            self.client.get(reverse('tracking:api_dashboard'))
        self.assertIsNone(resumen.call_args.args[7])

    def test_rechaza_una_ruta_larguisima(self):
        r = self.client.get(reverse('tracking:api_dashboard'),
                            {'ruta': 'X' * (views.MAX_LARGO_RUTA + 1)})
        self.assertEqual(r.status_code, 400)
        self.assertIn('demasiado largo', r.json()['error'])

    def test_el_tipo_llega_a_services(self):
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            r = self.client.get(reverse('tracking:api_dashboard'), {'tipo': 'buseton'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(resumen.call_args.args[6], 'BUSETON')

    def test_rechaza_tipo_desconocido(self):
        r = self.client.get(reverse('tracking:api_dashboard'), {'tipo': 'CAMIONETA'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('BUSETA', r.json()['error'])

    def test_las_horas_a_mano_llegan_a_services_en_minutos(self):
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            r = self.client.get(reverse('tracking:api_dashboard'),
                                {'hora_desde': '06:30', 'hora_hasta': '09:00'})
        self.assertEqual(r.status_code, 200)
        # El fin se corre un minuto: 09:00 se elige para verla, y la franja lo
        # tiene abierto.
        self.assertEqual(resumen.call_args.args[5], (6 * 60 + 30, 9 * 60 + 1))

    def test_las_horas_del_dia_entero_dan_la_vuelta_al_reloj(self):
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            self.client.get(reverse('tracking:api_dashboard'),
                            {'hora_desde': '00:00', 'hora_hasta': '23:59'})
        self.assertEqual(resumen.call_args.args[5], (0, 0))

    def test_rechaza_una_hora_invalida_o_a_medias(self):
        for params in ({'hora_desde': '25:00', 'hora_hasta': '09:00'},
                       {'hora_desde': '6:3', 'hora_hasta': '09:00'},
                       {'hora_desde': '06:30'}):
            r = self.client.get(reverse('tracking:api_dashboard'), params)
            self.assertEqual(r.status_code, 400, params)
            self.assertIn('HH:MM', r.json()['error'])

    @patch.object(services.api_client, 'get_vehicles')
    def test_un_fallo_del_api_responde_502_y_no_revienta(self, vehiculos):
        vehiculos.side_effect = services.api_client.ApiError('el API se cayó')
        with self.assertLogs('tracking.views', level='ERROR'):
            r = self.client.get(reverse('tracking:api_dashboard'))
        self.assertEqual(r.status_code, 502)
        self.assertIn('error', r.json())


class CatalogoDeCuentasTests(TestCase):
    """El catálogo se planta si el .env deja una cuenta mal configurada.

    Las cuatro fallas de aquí abajo abren la puerta en silencio: con un
    diccionario por comprensión se ven igual de bien que una configuración
    correcta, y el sitio arranca con el hueco puesto. Tienen que reventar al
    arrancar, no al primer login raro.
    """

    CUENTAS = (
        ('ADMIN', 'admin@rastrelital.com', 'Admin', None),
        ('PROCAPS', 'procaps@rastrelital.com', 'Procaps', ('PROCAPS',)),
    )

    def armar(self, **entorno):
        """Arma el catálogo con un entorno limpio más lo que se le pase."""
        limpio = {k: v for k, v in os.environ.items()
                  if not k.startswith('DASHBOARD_')}
        limpio.update(entorno)
        with patch.dict(os.environ, limpio, clear=True):
            return _catalogo_de(self.CUENTAS, con_defectos=True)

    def test_la_configuracion_buena_arma_el_catalogo(self):
        catalogo = self.armar()
        self.assertEqual(sorted(catalogo),
                         ['admin@rastrelital.com', 'procaps@rastrelital.com'])
        self.assertIsNone(catalogo['admin@rastrelital.com']['empresas'])

    def test_el_entorno_manda_sobre_el_correo_y_la_clave(self):
        catalogo = self.armar(DASHBOARD_CORREO_ADMIN='  Samuel@Rastrelital.COM ',
                              DASHBOARD_CLAVE_ADMIN='otra')
        self.assertIn('samuel@rastrelital.com', catalogo)
        self.assertEqual(catalogo['samuel@rastrelital.com']['clave'], 'otra')

    def test_rechaza_el_correo_en_blanco(self):
        """Una llave vacía deja entrar con el campo del correo vacío."""
        for vacio in ('', '   '):
            with self.assertRaisesMessage(ImproperlyConfigured,
                                          'DASHBOARD_CORREO_ADMIN'):
                self.armar(DASHBOARD_CORREO_ADMIN=vacio,
                           DASHBOARD_CLAVE_ADMIN='Cl4ve')

    def test_rechaza_lo_que_no_es_un_correo(self):
        """Si no lleva '@', el aviso del login mentiría: la cuenta existe."""
        with self.assertRaisesMessage(ImproperlyConfigured, 'no es un correo'):
            self.armar(DASHBOARD_CORREO_ADMIN='admin',
                       DASHBOARD_CLAVE_ADMIN='Cl4ve')

    def test_rechaza_el_correo_repetido(self):
        """Gana el último: la primera cuenta desaparece y cambian los permisos."""
        with self.assertRaisesMessage(ImproperlyConfigured, 'repite el correo'):
            self.armar(DASHBOARD_CORREO_ADMIN='jefe@procaps.com',
                       DASHBOARD_CLAVE_ADMIN='Cl4ve',
                       DASHBOARD_CORREO_PROCAPS='jefe@procaps.com',
                       DASHBOARD_CLAVE_PROCAPS='Otra')

    def test_rechaza_la_clave_en_blanco(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'sin escribir nada'):
            self.armar(DASHBOARD_CORREO_ADMIN='admin@rastrelital.com',
                       DASHBOARD_CLAVE_ADMIN='')

    def test_rechaza_media_cuenta_configurada(self):
        """Con una sola variable, la otra mitad se quedaría con el ejemplo.

        Y el ejemplo está en un repositorio público, así que configurar solo
        el correo dejaría la cuenta abierta con la clave que todos ven.
        """
        for a_medias in ({'DASHBOARD_CORREO_ADMIN': 'jefe@procaps.com'},
                         {'DASHBOARD_CLAVE_ADMIN': 'Cl4ve'}):
            with self.assertRaisesMessage(ImproperlyConfigured, 'van juntas'):
                self.armar(**a_medias)

    def test_en_produccion_no_hay_cuentas_sin_configurar(self):
        """Sin `con_defectos`, las claves del código no abren nada.

        Es lo que separa desarrollo de producción: en el servidor, el acceso
        de emergencia existe solo si alguien lo configuró a propósito.
        """
        limpio = {k: v for k, v in os.environ.items()
                  if not k.startswith('DASHBOARD_')}
        with patch.dict(os.environ, limpio, clear=True):
            self.assertEqual(_catalogo_de(self.CUENTAS, con_defectos=False), {})

        limpio['DASHBOARD_CORREO_ADMIN'] = 'rescate@rastrelital.com'
        limpio['DASHBOARD_CLAVE_ADMIN'] = 'Cl4ve'
        with patch.dict(os.environ, limpio, clear=True):
            catalogo = _catalogo_de(self.CUENTAS, con_defectos=False)
        self.assertEqual(list(catalogo), ['rescate@rastrelital.com'])


@con_catalogo
class CorreosDeCualquierFormaTests(TestCase):
    """El correo puede ser el que sea: no hay dominio privilegiado.

    Los correos que trae el código por defecto son `@rastrelital.com`, pero
    eso es solo el valor de desarrollo: en producción cada cuenta usa el
    correo real de su persona, del dominio que sea. Estas pruebas le cierran
    la puerta a que alguien, más adelante, ate el login a un dominio.
    """

    DIRECCIONES = (
        'samuel@gmail.com',
        'samuel.perez@procaps.com.co',
        'samuel+gps@gmail.com',
        's@x.io',
        'jefe-de-flota@ditar-transportes.com.co',
        'usuario_1@sub.dominio.example.com',
        "o'brien@example.com",
        'coordinacion.de.transporte.especial@transportes-del-caribe.com.co',
    )

    def setUp(self):
        cache.clear()
        parche = patch.object(views, '_lanzar_precalentamiento',
                              return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def test_entra_con_cualquier_correo(self):
        for correo in self.DIRECCIONES:
            with self.subTest(correo=correo):
                cache.clear()
                with self.settings(DASHBOARD_USUARIOS={
                        correo: {'clave': 'Cl4ve', 'empresas': None}}):
                    self.client.post(reverse('tracking:logout'))
                    r = self.client.post(reverse('tracking:login'),
                                         {'correo': f'  {correo.upper()}  ',
                                          'clave': 'Cl4ve'})
                    self.assertEqual(r.status_code, 302)
                    self.assertEqual(self.client.session.get(CLAVE_USUARIO),
                                     correo)

    def test_el_catalogo_acepta_cualquier_dominio(self):
        for correo in self.DIRECCIONES:
            with self.subTest(correo=correo):
                limpio = {k: v for k, v in os.environ.items()
                          if not k.startswith('DASHBOARD_')}
                limpio['DASHBOARD_CORREO_ADMIN'] = f'  {correo.upper()}  '
                limpio['DASHBOARD_CLAVE_ADMIN'] = 'Cl4ve'
                with patch.dict(os.environ, limpio, clear=True):
                    catalogo = _catalogo_de(
                        (('ADMIN', 'x@y.com', 'Admin', None),),
                        con_defectos=True)
                self.assertEqual(list(catalogo), [correo])

    def test_la_barra_escapa_el_correo(self):
        """El correo se pinta en la cabecera, así que no puede inyectar HTML."""
        hostil = 'x<script>alert(1)</script>@evil.com'
        with self.settings(DASHBOARD_USUARIOS={hostil: {'clave': 'Cl4ve',
                                                        'empresas': None}}):
            self.client.post(reverse('tracking:login'),
                             {'correo': hostil, 'clave': 'Cl4ve'})
            r = self.client.get(reverse('tracking:dashboard'))
        html = r.content.decode()
        self.assertNotIn(hostil, html)
        self.assertIn('&lt;script&gt;', html)


@rapido_al_hashear
@override_settings(DASHBOARD_USUARIOS={})
class CuentasEnLaBaseTests(TestCase):
    """Las cuentas de /admin/: una fila por persona, con la clave hasheada.

    El catálogo de settings va vacío a propósito, para que lo que se pruebe
    aquí sea la tabla y no el acceso de emergencia.
    """

    def setUp(self):
        cache.clear()
        self.login_url = reverse('tracking:login')
        parche = patch.object(views, '_lanzar_precalentamiento',
                              return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def crear(self, correo='jefe@procaps.com.co', clave='Cl4veLarga',
              empresas='PROCAPS', acceso_total=False, activo=True):
        usuario = DashboardUsuario(correo=correo, empresas=empresas,
                                   acceso_total=acceso_total, activo=activo)
        usuario.set_clave(clave)
        usuario.save()
        return usuario

    def entrar(self, correo, clave):
        return self.client.post(self.login_url, {'correo': correo,
                                                 'clave': clave})

    def test_una_cuenta_de_la_tabla_entra(self):
        self.crear()
        r = self.entrar('jefe@procaps.com.co', 'Cl4veLarga')
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)
        self.assertEqual(self.client.session.get(CLAVE_USUARIO),
                         'jefe@procaps.com.co')

    def test_la_clave_no_se_guarda_en_claro(self):
        """Es la diferencia con el catálogo de settings, que la guarda tal cual."""
        usuario = self.crear(clave='Cl4veLarga')
        self.assertNotIn('Cl4veLarga', usuario.clave_hash)
        self.assertTrue(usuario.check_clave('Cl4veLarga'))
        self.assertFalse(usuario.check_clave('otra'))

    def test_la_clave_equivocada_no_entra(self):
        self.crear()
        self.entrar('jefe@procaps.com.co', 'otra')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_el_correo_se_normaliza_al_guardar(self):
        usuario = self.crear(correo='  JEFE@Procaps.COM.CO  ')
        self.assertEqual(usuario.correo, 'jefe@procaps.com.co')
        self.entrar('JEFE@PROCAPS.COM.CO', 'Cl4veLarga')
        self.assertTrue(self.client.session.get(CLAVE_SESION))

    def test_una_cuenta_inactiva_no_entra(self):
        self.crear(activo=False)
        self.entrar('jefe@procaps.com.co', 'Cl4veLarga')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_desactivar_cierra_la_sesion_que_ya_estaba_abierta(self):
        """Los permisos se releen en cada petición, para que esto pase."""
        usuario = self.crear()
        self.entrar('jefe@procaps.com.co', 'Cl4veLarga')
        self.assertEqual(self.client.get(reverse('tracking:dashboard')).status_code,
                         200)

        usuario.activo = False
        usuario.save(update_fields=['activo'])

        r = self.client.get(reverse('tracking:dashboard'))
        self.assertEqual(r.status_code, 302)
        self.assertIn(self.login_url, r.headers['Location'])

    def test_cada_cuenta_ve_solo_lo_suyo(self):
        self.crear(empresas='PROCAPS')
        self.entrar('jefe@procaps.com.co', 'Cl4veLarga')

        html = self.client.get(reverse('tracking:dashboard')).content.decode()
        self.assertIn('data-empresa="PROCAPS"', html)
        self.assertNotIn('data-empresa="DITAR"', html)

        r = self.client.get(reverse('tracking:api_dashboard'), {'empresa': 'DITAR'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get(reverse('tracking:api_fleet')).status_code,
                         403)

    def test_dos_empresas_en_una_cuenta(self):
        self.crear(empresas='PROCAPS,DITAR')
        self.entrar('jefe@procaps.com.co', 'Cl4veLarga')
        html = self.client.get(reverse('tracking:dashboard')).content.decode()
        for propia in ('PROCAPS', 'DITAR'):
            self.assertIn(f'data-empresa="{propia}"', html)
        self.assertNotIn('data-empresa="RELIANZ"', html)

    def test_el_acceso_total_ve_todo_y_el_mapa(self):
        self.crear(correo='samuel@rastrelital.com', empresas='',
                   acceso_total=True)
        self.entrar('samuel@rastrelital.com', 'Cl4veLarga')
        html = self.client.get(reverse('tracking:dashboard')).content.decode()
        for empresa in services.EMPRESAS:
            self.assertIn(f'data-empresa="{empresa}"', html)
        self.assertEqual(self.client.get(reverse('tracking:fleet')).status_code, 200)

    def test_sin_empresas_y_sin_acceso_total_no_ve_ningun_viaje(self):
        """El vacío significa «nada», no «todo»: por eso son dos campos."""
        self.crear(empresas='', acceso_total=False)
        self.entrar('jefe@procaps.com.co', 'Cl4veLarga')
        r = self.client.get(reverse('tracking:api_dashboard'),
                            {'empresa': 'PROCAPS'})
        self.assertEqual(r.status_code, 403)

    def test_se_anota_el_ultimo_ingreso(self):
        usuario = self.crear()
        self.assertIsNone(usuario.ultimo_ingreso)
        self.entrar('jefe@procaps.com.co', 'Cl4veLarga')
        usuario.refresh_from_db()
        self.assertIsNotNone(usuario.ultimo_ingreso)

    @override_settings(DASHBOARD_USUARIOS={
        'jefe@procaps.com.co': {'clave': 'LaDeSettings', 'empresas': None}})
    def test_la_tabla_le_gana_al_acceso_de_emergencia(self):
        """Con la cuenta dada de alta, la de settings deja de contar.

        Si no, cambiarle la clave a alguien en /admin/ no serviría de nada
        mientras su correo siguiera en una variable de entorno.
        """
        self.crear(empresas='PROCAPS')
        self.entrar('jefe@procaps.com.co', 'LaDeSettings')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

        self.entrar('jefe@procaps.com.co', 'Cl4veLarga')
        self.assertTrue(self.client.session.get(CLAVE_SESION))
        # Y manda el techo de la tabla, no el acceso total de settings.
        self.assertEqual(self.client.get(reverse('tracking:fleet')).status_code,
                         302)


@rapido_al_hashear
class AdminDeCuentasTests(TestCase):
    """El formulario de /admin/, que es por donde se dan de alta las personas."""

    def datos(self, **cambios):
        base = {'correo': 'jefe@procaps.com.co', 'nombre': 'Jefe',
                'clave': 'Cl4veLarga', 'empresas': ['PROCAPS'],
                'acceso_total': False, 'activo': True}
        base.update(cambios)
        return base

    def test_crea_la_cuenta_con_la_clave_hasheada(self):
        form = DashboardUsuarioForm(self.datos())
        self.assertTrue(form.is_valid(), form.errors)
        usuario = form.save()
        self.assertNotIn('Cl4veLarga', usuario.clave_hash)
        self.assertTrue(usuario.check_clave('Cl4veLarga'))
        self.assertEqual(usuario.empresas, 'PROCAPS')

    def test_las_casillas_se_guardan_separadas_por_comas(self):
        form = DashboardUsuarioForm(self.datos(empresas=['PROCAPS', 'DITAR']))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().empresas, 'PROCAPS,DITAR')

    def test_al_crear_pide_clave(self):
        form = DashboardUsuarioForm(self.datos(clave=''))
        self.assertFalse(form.is_valid())
        self.assertIn('clave', form.errors)

    def test_al_editar_la_clave_en_blanco_conserva_la_de_antes(self):
        usuario = DashboardUsuarioForm(self.datos()).save()
        antes = usuario.clave_hash
        form = DashboardUsuarioForm(self.datos(clave=''), instance=usuario)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().clave_hash, antes)

    def test_no_deja_una_cuenta_sin_empresas_ni_acceso_total(self):
        """Entraría a un dashboard vacío, y nadie sabría por qué."""
        form = DashboardUsuarioForm(self.datos(empresas=[]))
        self.assertFalse(form.is_valid())
        self.assertIn('empresas', form.errors)

    def test_con_acceso_total_no_hacen_falta_las_casillas(self):
        form = DashboardUsuarioForm(self.datos(empresas=[], acceso_total=True))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.save().empresas_tuple)

    def test_al_editar_vuelve_a_marcar_las_casillas_guardadas(self):
        usuario = DashboardUsuarioForm(
            self.datos(empresas=['PROCAPS', 'DITAR'])).save()
        form = DashboardUsuarioForm(instance=usuario)
        self.assertEqual(form.initial['empresas'], ['PROCAPS', 'DITAR'])


@rapido_al_hashear
class ComandoCrearUsuarioTests(TestCase):
    """El comando de consola: la primera cuenta, cuando aún no hay admin."""

    def correr(self, *args, **opciones):
        salida = StringIO()
        call_command('crear_usuario_dashboard', *args, stdout=salida, **opciones)
        return salida.getvalue()

    def test_crea_una_cuenta(self):
        self.correr('jefe@procaps.com.co', clave='Cl4veLarga',
                    empresas='PROCAPS')
        usuario = DashboardUsuario.objects.get(correo='jefe@procaps.com.co')
        self.assertTrue(usuario.check_clave('Cl4veLarga'))
        self.assertEqual(usuario.empresas_tuple, ('PROCAPS',))

    def test_crea_una_cuenta_con_acceso_total(self):
        self.correr('samuel@rastrelital.com', clave='Cl4veLarga',
                    acceso_total=True)
        usuario = DashboardUsuario.objects.get(correo='samuel@rastrelital.com')
        self.assertIsNone(usuario.empresas_tuple)

    def test_actualiza_la_clave_de_una_cuenta_que_ya_existe(self):
        self.correr('jefe@procaps.com.co', clave='Vieja', empresas='PROCAPS')
        self.correr('jefe@procaps.com.co', clave='Nueva', empresas='PROCAPS')
        usuario = DashboardUsuario.objects.get(correo='jefe@procaps.com.co')
        self.assertTrue(usuario.check_clave('Nueva'))
        self.assertEqual(DashboardUsuario.objects.count(), 1)

    def test_desactiva_una_cuenta(self):
        self.correr('jefe@procaps.com.co', clave='Cl4veLarga',
                    empresas='PROCAPS')
        self.correr('jefe@procaps.com.co', desactivar=True)
        self.assertFalse(
            DashboardUsuario.objects.get(correo='jefe@procaps.com.co').activo)

    def test_rechaza_lo_que_no_es_un_correo(self):
        with self.assertRaisesMessage(CommandError, 'no es un correo'):
            self.correr('admin', clave='Cl4veLarga', empresas='PROCAPS')

    def test_rechaza_una_empresa_inventada(self):
        with self.assertRaisesMessage(CommandError, 'Empresa desconocida'):
            self.correr('jefe@x.com', clave='Cl4veLarga', empresas='INVENTADA')

    def test_rechaza_una_cuenta_sin_empresas_ni_acceso_total(self):
        with self.assertRaisesMessage(CommandError, '--acceso-total'):
            self.correr('jefe@x.com', clave='Cl4veLarga')

    def test_desactivar_una_cuenta_que_no_existe_avisa(self):
        with self.assertRaisesMessage(CommandError, 'No hay ninguna cuenta'):
            self.correr('nadie@x.com', desactivar=True)
