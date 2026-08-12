"""Pruebas del dashboard. El API va simulado: la suite no toca la red."""

from datetime import date, datetime
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from . import services, views
from .middleware import CLAVE_SESION, CLAVE_USUARIO


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
    """A qué empresa se le apunta cada timbrada."""

    SERVICIOS = [('08:00:00', 'PROCAPS'), ('14:00:00', 'DITAR')]

    def test_toma_el_servicio_siguiente(self):
        self.assertEqual(
            services._empresa_de_timbrada('07:30:00', self.SERVICIOS), 'PROCAPS')
        self.assertEqual(
            services._empresa_de_timbrada('10:00:00', self.SERVICIOS), 'DITAR')

    def test_despues_del_ultimo_usa_el_ultimo(self):
        self.assertEqual(
            services._empresa_de_timbrada('23:00:00', self.SERVICIOS), 'DITAR')

    def test_sin_servicios_no_hay_empresa(self):
        self.assertIsNone(services._empresa_de_timbrada('10:00:00', []))


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
        eventos.side_effect = lambda equipo, *a, **k: (
            [{'fecha': '2026-07-20', 'hora': '08:30:00', 'pasajero': 'a'}] * 31
            if equipo == '100' else [])

        r = services.range_summary('2026-07-20', '2026-07-20')

        por_interno = {v['interno']: v for v in r['vehiculos']}
        self.assertEqual(por_interno['INT 7076']['servicios'], 2)
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
            [{'fecha': '2026-07-20', 'hora': '08:30:00', 'pasajero': 'a'}] * 58)

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

    def test_el_detalle_trae_una_fila_por_dia(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = []
        eventos.return_value = []
        r = services.range_summary('2026-07-20', '2026-07-22')
        self.assertEqual([f['fecha'] for f in r['detalle']['filas']],
                         ['2026-07-20', '2026-07-21', '2026-07-22'])


class SinBaseDeDatosTests(TestCase):
    """Entrar y ver el dashboard no escribe una sola fila."""

    def test_la_sesion_va_en_cookie_firmada(self):
        self.assertEqual(settings.SESSION_ENGINE,
                         'django.contrib.sessions.backends.signed_cookies')

    def test_entrar_no_escribe_en_la_base_de_datos(self):
        with self.assertNumQueries(0):
            r = self.client.post(reverse('tracking:login'),
                                 {'usuario': 'admin', 'clave': 'Admin'})
        self.assertEqual(r.status_code, 302)

    def test_ver_el_dashboard_no_escribe_en_la_base_de_datos(self):
        self.client.post(reverse('tracking:login'),
                         {'usuario': 'admin', 'clave': 'Admin'})
        with self.assertNumQueries(0):
            self.client.get(reverse('tracking:dashboard'))


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
        self.client.post(self.login_url, {'usuario': 'x', 'clave': 'y'})
        hilos.Thread.assert_not_called()

    @patch.object(views, 'threading')
    def test_con_sesion_iniciada_no_precalienta(self, hilos):
        self.client.post(self.login_url, {'usuario': 'admin', 'clave': 'Admin'})
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
        r = self.client.post(self.login_url, {'usuario': 'admin', 'clave': 'Admin'})
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)
        self.assertTrue(self.client.session.get(CLAVE_SESION))

    def test_credenciales_incorrectas(self):
        r = self.client.post(self.login_url, {'usuario': 'admin', 'clave': 'mala'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'incorrectos')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_respeta_next(self):
        destino = reverse('tracking:fleet')
        r = self.client.post(self.login_url,
                             {'usuario': 'admin', 'clave': 'Admin', 'next': destino})
        self.assertRedirects(r, destino, fetch_redirect_response=False)

    def test_no_sirve_de_trampolin_a_otro_sitio(self):
        r = self.client.post(self.login_url, {
            'usuario': 'admin', 'clave': 'Admin',
            'next': 'https://sitio-malo.example/x'})
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)

    def test_salir_cierra_la_sesion_solo_por_post(self):
        self.client.post(self.login_url, {'usuario': 'admin', 'clave': 'Admin'})
        salir = reverse('tracking:logout')

        self.client.get(salir)
        self.assertTrue(self.client.session.get(CLAVE_SESION))

        self.client.post(salir)
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_freno_tras_varios_intentos_fallidos(self):
        for _ in range(views.MAX_INTENTOS):
            self.client.post(self.login_url, {'usuario': 'x', 'clave': 'y'})
        r = self.client.post(self.login_url, {'usuario': 'admin', 'clave': 'Admin'})
        self.assertContains(r, 'Demasiados intentos')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_el_admin_conserva_su_propio_login(self):
        # Ahora el login vive en '/', así que "no contiene el login" sería
        # cierto para cualquier ruta: lo que se comprueba es que el admin
        # redirige al suyo.
        r = self.client.get('/admin/')
        self.assertTrue(r.headers.get('Location', '').startswith('/admin/login'))


class AccesoPorEmpresaTests(TestCase):
    """Cada usuario ve solo los viajes de su empresa, también en el JSON."""

    def setUp(self):
        cache.clear()
        self.login_url = reverse('tracking:login')
        parche = patch.object(views, '_lanzar_precalentamiento',
                              return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def entrar(self, usuario, clave):
        return self.client.post(self.login_url, {'usuario': usuario, 'clave': clave})

    def test_los_cuatro_usuarios_entran(self):
        for usuario, clave in (('admin', 'Admin'), ('procaps', 'Procaps'),
                               ('ditar', 'Ditar'), ('relianz', 'relianz')):
            self.client.post(reverse('tracking:logout'))
            cache.clear()
            self.entrar(usuario, clave)
            self.assertEqual(self.client.session.get(CLAVE_USUARIO), usuario)

    def test_el_usuario_no_distingue_mayusculas_pero_la_clave_si(self):
        self.entrar('PROCAPS', 'Procaps')
        self.assertEqual(self.client.session.get(CLAVE_USUARIO), 'procaps')

        self.client.post(reverse('tracking:logout'))
        self.entrar('procaps', 'procaps')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_cada_usuario_dibuja_solo_sus_pestanas(self):
        casos = {
            'procaps': ('PROCAPS', ('DITAR', 'RELIANZ')),
            'ditar':   ('DITAR', ('PROCAPS', 'RELIANZ')),
            'relianz': ('RELIANZ', ('PROCAPS', 'DITAR')),
        }
        for usuario, (propia, ajenas) in casos.items():
            self.client.post(reverse('tracking:logout'))
            cache.clear()
            self.entrar(usuario, {'procaps': 'Procaps', 'ditar': 'Ditar',
                                  'relianz': 'relianz'}[usuario])
            r = self.client.get(reverse('tracking:dashboard'))
            self.assertContains(r, f'data-empresa="{propia}"')
            self.assertContains(r, f'data-empresa="{services.TAB_SIN_IDENTIFICAR}"')
            for ajena in ajenas:
                self.assertNotContains(r, f'data-empresa="{ajena}"')

    def test_el_admin_dibuja_todas_las_pestanas(self):
        self.entrar('admin', 'Admin')
        r = self.client.get(reverse('tracking:dashboard'))
        for valor in services.EMPRESAS:
            self.assertContains(r, f'data-empresa="{valor}"')

    def test_el_json_rechaza_la_empresa_ajena(self):
        self.entrar('procaps', 'Procaps')
        r = self.client.get(reverse('tracking:api_dashboard'), {'empresa': 'DITAR'})
        self.assertEqual(r.status_code, 403)
        self.assertIn('no tiene acceso', r.json()['error'])

    def test_el_json_acepta_la_empresa_propia_y_la_sin_identificar(self):
        self.entrar('procaps', 'Procaps')
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            for empresa in ('PROCAPS', services.TAB_SIN_IDENTIFICAR):
                r = self.client.get(reverse('tracking:api_dashboard'),
                                    {'empresa': empresa})
                self.assertEqual(r.status_code, 200, empresa)
        self.assertEqual(resumen.call_count, 2)

    def test_el_json_le_pasa_el_techo_del_usuario_a_services(self):
        self.entrar('ditar', 'Ditar')
        with patch.object(services, 'range_summary', return_value={}) as resumen:
            self.client.get(reverse('tracking:api_dashboard'),
                            {'desde': '2026-07-20', 'hasta': '2026-07-20'})
        resumen.assert_called_once_with(
            '2026-07-20', '2026-07-20', None,
            ['DITAR', services.TAB_SIN_IDENTIFICAR], None, None, None)

    def test_el_mapa_de_flota_es_solo_del_admin(self):
        self.entrar('relianz', 'relianz')
        r = self.client.get(reverse('tracking:fleet'))
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)
        r = self.client.get(reverse('tracking:api_fleet'))
        self.assertEqual(r.status_code, 403)

    def test_el_admin_si_ve_el_mapa(self):
        self.entrar('admin', 'Admin')
        self.assertEqual(self.client.get(reverse('tracking:fleet')).status_code, 200)

    def test_quitar_un_usuario_del_catalogo_cierra_su_sesion(self):
        self.entrar('procaps', 'Procaps')
        catalogo = {k: v for k, v in settings.DASHBOARD_USUARIOS.items()
                    if k != 'procaps'}
        with self.settings(DASHBOARD_USUARIOS=catalogo):
            r = self.client.get(reverse('tracking:dashboard'))
        self.assertEqual(r.status_code, 302)
        self.assertIn(self.login_url, r.headers['Location'])


class PestanaTests(TestCase):
    """Cerrar la pestaña obliga a escribir usuario y contraseña otra vez."""

    def setUp(self):
        cache.clear()
        self.login_url = reverse('tracking:login')
        parche = patch.object(views, '_lanzar_precalentamiento',
                              return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def entrar(self):
        self.client.post(self.login_url, {'usuario': 'admin', 'clave': 'Admin'})

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
                         {'usuario': 'admin', 'clave': 'Admin', 'next': destino})
        r = self.client.get(destino)
        self.assertTrue(r.context['sesion_nueva'])

    def test_el_login_no_lleva_guardia(self):
        r = self.client.get(self.login_url)
        self.assertNotContains(r, 'rastrelital_pestana')

    def test_el_cierre_de_la_guardia_pasa_el_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.get(self.login_url)
        cliente.post(self.login_url, {
            'usuario': 'admin', 'clave': 'Admin',
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


class DashboardTests(TestCase):
    """La página del dashboard y los errores de su endpoint JSON."""

    def setUp(self):
        cache.clear()
        self.client.post(reverse('tracking:login'),
                         {'usuario': 'admin', 'clave': 'Admin'})

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
        self.assertContains(r, 'id="dias-custom"')
        self.assertContains(r, 'id="hora-desde"')
        for valor in services.TURNOS:
            self.assertContains(r, f'data-turno="{valor}"')

    def test_dibuja_el_select_de_tipo_de_vehiculo(self):
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertContains(r, 'id="sel-tipo"')
        for valor in services.TIPOS:
            self.assertContains(r, f'data-tipo="{valor}"')

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
