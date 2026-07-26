"""Pruebas de la app tracking.

Ninguna prueba toca el API de Service24GPS: todo lo que sale a la red
(:mod:`tracking.api_client`) se reemplaza con ``mock``, así que la suite
corre sin credenciales, sin internet y en un par de segundos.

Ejecutar con::

    python manage.py test
"""

from datetime import date, datetime
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from . import services, views
from .middleware import CLAVE_SESION


def _alerta(equipo, hora, geocerca, fecha='2026-07-20', fuera=False):
    """Arma una alerta con la forma exacta que devuelve el API.

    Args:
        equipo: Identificador del equipo GPS.
        hora: Hora de la alerta (``HH:MM:SS``).
        geocerca: Nombre de la geocerca a incrustar en la descripción.
        fecha: Fecha de la alerta, en formato ``YYYY-MM-DD``.
        fuera: Si es True, arma una alerta de SALIDA de la geocerca.

    Returns:
        Diccionario de alerta listo para usar en las pruebas.
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
    """Extracción del nombre de la geocerca del texto de la alerta."""

    def test_extrae_el_nombre(self):
        self.assertEqual(
            services._nombre_geocerca(_alerta('1', '08:00:00', 'PROCAPS')),
            'PROCAPS')

    def test_deshace_entidades_html(self):
        """El API escapa el texto: 'RUTA 3 &amp; 4' debe volver a '&'."""
        alerta = _alerta('1', '08:00:00', 'RUTA 3 &amp; 4')
        self.assertEqual(services._nombre_geocerca(alerta), 'RUTA 3 & 4')

    def test_sin_patron_devuelve_vacio(self):
        self.assertEqual(services._nombre_geocerca({'Descripcion': 'otra cosa'}), '')
        self.assertEqual(services._nombre_geocerca({}), '')


class EmpresaDeGeocercaTests(TestCase):
    """La empresa se infiere del nombre de la geocerca y de nada más."""

    def test_procaps_va_anclado_al_inicio(self):
        self.assertEqual(services.empresa_de_geocerca('PROCAPS'), 'PROCAPS')
        self.assertEqual(services.empresa_de_geocerca('procaps planta'), 'PROCAPS')
        self.assertEqual(services.empresa_de_geocerca('RUTA 12'), 'PROCAPS')
        # "PROCAPS" en medio del nombre NO cuenta: el patrón lleva ^.
        self.assertIsNone(services.empresa_de_geocerca('MI PROCAPS'))

    def test_ditar_y_relianz_en_cualquier_parte(self):
        """Esas geocercas aún no existen: deben reconocerse se llamen como se llamen."""
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
    """Cada empresa tiene pestaña propia; lo no atribuible cae en una aparte."""

    def test_cada_empresa_a_su_pestana(self):
        for e in ('PROCAPS', 'DITAR', 'RELIANZ'):
            self.assertEqual(services.tab_de_empresa(e), e)

    def test_lo_desconocido_cae_en_sin_identificar(self):
        self.assertEqual(services.tab_de_empresa(None), services.TAB_SIN_IDENTIFICAR)
        self.assertEqual(services.tab_de_empresa('OTRA'), services.TAB_SIN_IDENTIFICAR)

    def test_todas_las_pestanas_tienen_etiqueta(self):
        self.assertEqual(sorted(services.ETIQUETA_EMPRESA), sorted(services.EMPRESAS))


class EntradaGeocercaTests(TestCase):
    """Solo las ENTRADAS cuentan como servicio; las salidas se descartan."""

    def test_entrada_cuenta(self):
        self.assertTrue(services._es_entrada_geocerca(_alerta('1', '08:00:00', 'PROCAPS')))

    def test_salida_no_cuenta(self):
        salida = _alerta('1', '09:00:00', 'PROCAPS', fuera=True)
        self.assertFalse(services._es_entrada_geocerca(salida))

    def test_otra_alerta_no_cuenta(self):
        self.assertFalse(services._es_entrada_geocerca(
            {'TipoAlerta': 'EXCESO DE VELOCIDAD', 'StatusAlerta': '', 'Descripcion': ''}))


class EmpresaDeTimbradaTests(TestCase):
    """Una timbrada se atribuye a la empresa del siguiente servicio del bus."""

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
    """La capacidad se busca por interno normalizado (sin espacios, mayúsculas)."""

    def test_normaliza(self):
        self.assertEqual(services._norm_interno('INT 7074'), 'INT7074')
        self.assertEqual(services._norm_interno('  int  7074 '), 'INT7074')
        self.assertEqual(services._norm_interno(None), '')

    def test_la_capacidad_se_encuentra(self):
        self.assertEqual(
            services.CAPACIDAD_POR_INTERNO[services._norm_interno('INT 7076')], 31)


@patch.object(services.api_client, 'get_alerts')
@patch.object(services.api_client, 'get_vehicles')
@patch.object(services.api_client, 'get_passenger_events')
class RangeSummaryTests(TestCase):
    """El cálculo completo de ocupación, con el API simulado.

    Los decoradores entregan los mocks al revés de como se escriben: el
    de más abajo (``get_passenger_events``) llega primero.
    """

    # Dos buses: uno con capacidad conocida (INT 7076 = 31) y otro sin ella.
    VEHICULOS = [
        {'idgps': '100', 'nombre': 'INT 7076'},
        {'idgps': '200', 'nombre': 'INT 9999'},
    ]

    def setUp(self):
        cache.clear()

    def test_cuenta_servicios_timbradas_y_ocupacion(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS
        # El bus 100 entra 2 veces a PROCAPS…
        alertas.return_value = [_alerta('100', '08:00:00', 'PROCAPS'),
                                _alerta('100', '14:00:00', 'PROCAPS')]
        # …y timbra 31 pasajeros en total.
        eventos.side_effect = lambda equipo, *a, **k: (
            [{'fecha': '2026-07-20', 'hora': '08:30:00', 'pasajero': 'a'}] * 31
            if equipo == '100' else [])

        r = services.range_summary('2026-07-20', '2026-07-20')

        por_interno = {v['interno']: v for v in r['vehiculos']}
        self.assertEqual(por_interno['INT 7076']['servicios'], 2)
        self.assertEqual(por_interno['INT 7076']['timbradas'], 31)
        self.assertEqual(por_interno['INT 7076']['capacidad'], 31)
        # 31 timbradas / (2 servicios x 31 asientos) = 50 %
        self.assertEqual(por_interno['INT 7076']['ocupacion'], 50)
        self.assertEqual(por_interno['INT 7076']['capacidad_total'], 62)
        # Sin capacidad conocida no se inventa un porcentaje.
        self.assertIsNone(por_interno['INT 9999']['ocupacion'])
        self.assertEqual(r['unidades_con_error'], 0)

    def test_filtra_por_empresa(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = [_alerta('100', '08:00:00', 'PROCAPS'),
                                _alerta('100', '14:00:00', 'DITAR')]
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '07:00:00', 'pasajero': 'a'},   # -> PROCAPS
            {'fecha': '2026-07-20', 'hora': '13:00:00', 'pasajero': 'b'},   # -> DITAR
        ]

        procaps = services.range_summary('2026-07-20', '2026-07-20', 'PROCAPS')
        ditar = services.range_summary('2026-07-20', '2026-07-20', 'DITAR')

        self.assertEqual(procaps['vehiculos'][0]['servicios'], 1)
        self.assertEqual(procaps['vehiculos'][0]['timbradas'], 1)
        self.assertEqual(ditar['vehiculos'][0]['servicios'], 1)
        self.assertEqual(ditar['vehiculos'][0]['timbradas'], 1)

    def test_las_timbradas_sin_servicio_caen_en_sin_identificar(self, eventos,
                                                                vehiculos, alertas):
        """Si el bus no entró a ninguna geocerca, su timbrada no se pierde."""
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = []          # ningún servicio ese día
        eventos.side_effect = lambda equipo, *a, **k: [
            {'fecha': '2026-07-20', 'hora': '09:00:00', 'pasajero': 'a'}]

        todas = services.range_summary('2026-07-20', '2026-07-20')
        sin_id = services.range_summary('2026-07-20', '2026-07-20',
                                        services.TAB_SIN_IDENTIFICAR)

        self.assertEqual(todas['timbradas_inferidas'], 1)
        self.assertEqual(sin_id['vehiculos'][0]['timbradas'], 1)

    def test_un_fallo_del_api_se_reporta_y_no_tumba_el_dashboard(self, eventos,
                                                                 vehiculos, alertas):
        """Una unidad caída no puede verse igual que una sin pasajeros."""
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
        self.assertEqual(len(r['vehiculos']), 2)   # el resto sí se calcula

    def test_invierte_el_rango_al_reves(self, eventos, vehiculos, alertas):
        vehiculos.return_value = []
        alertas.return_value = []
        eventos.return_value = []
        r = services.range_summary('2026-07-25', '2026-07-20')
        self.assertEqual((r['desde'], r['hasta']), ('2026-07-20', '2026-07-25'))

    def test_el_detalle_trae_una_fila_por_dia(self, eventos, vehiculos, alertas):
        vehiculos.return_value = self.VEHICULOS[:1]
        alertas.return_value = []
        eventos.return_value = []
        r = services.range_summary('2026-07-20', '2026-07-22')
        self.assertEqual([f['fecha'] for f in r['detalle']['filas']],
                         ['2026-07-20', '2026-07-21', '2026-07-22'])


class SinBaseDeDatosTests(TestCase):
    """El login no puede depender de la base de datos.

    En producción (Render) el build no corre ``migrate`` y el disco es
    efímero, así que la tabla ``django_session`` no existe: guardar la
    sesión en base de datos tumbaba el login entero con
    ``OperationalError: no such table: django_session``.
    """

    def test_la_sesion_va_en_cookie_firmada(self):
        from django.conf import settings
        self.assertEqual(settings.SESSION_ENGINE,
                         'django.contrib.sessions.backends.signed_cookies')

    def test_entrar_no_escribe_en_la_base_de_datos(self):
        """Iniciar sesión no debe ejecutar ni una sola consulta SQL."""
        with self.assertNumQueries(0):
            r = self.client.post(reverse('tracking:login'),
                                 {'usuario': '1234', 'clave': '1234'})
        self.assertEqual(r.status_code, 302)

    def test_ver_el_dashboard_no_escribe_en_la_base_de_datos(self):
        self.client.post(reverse('tracking:login'),
                         {'usuario': '1234', 'clave': '1234'})
        with self.assertNumQueries(0):
            self.client.get(reverse('tracking:dashboard'))


class PrecalentamientoTests(TestCase):
    """El login precalienta la consulta inicial mientras escriben la clave."""

    def setUp(self):
        cache.clear()          # la marca de "ya se está precalentando" vive ahí
        self.login_url = reverse('tracking:login')

    def test_el_rango_es_el_ultimo_mes(self):
        desde, hasta = services.rango_ultimo_mes()
        d1 = datetime.strptime(desde, '%Y-%m-%d').date()
        d2 = datetime.strptime(hasta, '%Y-%m-%d').date()
        self.assertEqual(d2, date.today())
        self.assertEqual((d2 - d1).days, services.DIAS_ULTIMO_MES)

    def test_precalienta_exactamente_ese_rango(self):
        """Si el rango difiere en un día, el cache no le sirve al navegador."""
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
        # Una segunda visita dentro de la ventana no lanza otro hilo.
        self.client.get(self.login_url)
        hilos.Thread.assert_called_once()

    @patch.object(views, 'threading')
    def test_el_post_no_lo_lanza(self, hilos):
        self.client.post(self.login_url, {'usuario': 'x', 'clave': 'y'})
        hilos.Thread.assert_not_called()

    @patch.object(views, 'threading')
    def test_con_sesion_iniciada_no_precalienta(self, hilos):
        """Con sesión, /entrar/ redirige antes de llegar al precalentamiento."""
        self.client.post(self.login_url, {'usuario': '1234', 'clave': '1234'})
        self.client.get(self.login_url)
        hilos.Thread.assert_not_called()

    def test_un_fallo_no_se_escapa_del_hilo(self):
        """El hilo corre sin nadie que lo espere: un error solo va al log."""
        with patch.object(views.services, 'precalentar_ultimo_mes',
                          side_effect=RuntimeError('boom')):
            with self.assertLogs('tracking.views', level='ERROR'):
                views._precalentar_dashboard()   # no debe lanzar excepción

    def test_sin_credenciales_solo_se_anota(self):
        with patch.object(views.services, 'precalentar_ultimo_mes',
                          side_effect=views.api_client.ApiConfigError('faltan')):
            with self.assertLogs('tracking.views', level='INFO') as log:
                views._precalentar_dashboard()
        self.assertIn('omitido', log.output[0])


class LoginTests(TestCase):
    """El acceso al dashboard: quién entra, quién no y cómo se frena."""

    def setUp(self):
        cache.clear()          # el freno de intentos vive en el cache
        self.login_url = reverse('tracking:login')
        # El GET del login lanza el precalentamiento en un hilo real que
        # consultaría el API: aquí se anula para que la suite siga sin red.
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

    def test_el_login_se_ve_sin_sesion(self):
        r = self.client.get(self.login_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Rastrelital')
        # Sin sesión no se muestra el menú, ni el cliente, ni el botón de salir.
        self.assertNotContains(r, 'Expreso Brasilia')
        self.assertNotContains(r, '>Salir<')

    def test_credenciales_correctas(self):
        r = self.client.post(self.login_url, {'usuario': '1234', 'clave': '1234'})
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)
        self.assertTrue(self.client.session.get(CLAVE_SESION))

    def test_credenciales_incorrectas(self):
        r = self.client.post(self.login_url, {'usuario': '1234', 'clave': 'mala'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'incorrectos')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_respeta_next(self):
        destino = reverse('tracking:fleet')
        r = self.client.post(self.login_url,
                             {'usuario': '1234', 'clave': '1234', 'next': destino})
        self.assertRedirects(r, destino, fetch_redirect_response=False)

    def test_no_sirve_de_trampolin_a_otro_sitio(self):
        r = self.client.post(self.login_url, {
            'usuario': '1234', 'clave': '1234',
            'next': 'https://sitio-malo.example/x'})
        self.assertRedirects(r, reverse('tracking:dashboard'),
                             fetch_redirect_response=False)

    def test_salir_cierra_la_sesion_solo_por_post(self):
        self.client.post(self.login_url, {'usuario': '1234', 'clave': '1234'})
        salir = reverse('tracking:logout')

        self.client.get(salir)   # un GET no debe cerrar nada
        self.assertTrue(self.client.session.get(CLAVE_SESION))

        self.client.post(salir)
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_freno_tras_varios_intentos_fallidos(self):
        for _ in range(views.MAX_INTENTOS):
            self.client.post(self.login_url, {'usuario': 'x', 'clave': 'y'})
        # Ahora ni la contraseña buena entra.
        r = self.client.post(self.login_url, {'usuario': '1234', 'clave': '1234'})
        self.assertContains(r, 'Demasiados intentos')
        self.assertFalse(self.client.session.get(CLAVE_SESION))

    def test_el_admin_conserva_su_propio_login(self):
        """El middleware no debe secuestrar /admin/, que ya se autentica solo."""
        r = self.client.get('/admin/')
        self.assertNotIn(self.login_url, r.headers.get('Location', ''))


class PestanaTests(TestCase):
    """Cerrar la pestaña tiene que volver a pedir usuario y contraseña.

    La cookie de sesión no alcanza para eso: la comparten todas las
    pestañas del navegador y sobrevive a cerrar una. Por eso el login
    marca la primera página que se pinta (``sesion_nueva``), esa página
    sella su pestaña en sessionStorage, y una pestaña sin sello cierra la
    sesión sola. Aquí se prueba la mitad servidor; la del navegador es el
    bloque "guardia_pestana" de base.html.
    """

    def setUp(self):
        cache.clear()
        self.login_url = reverse('tracking:login')
        parche = patch.object(views, '_lanzar_precalentamiento',
                              return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def entrar(self):
        self.client.post(self.login_url, {'usuario': '1234', 'clave': '1234'})

    def test_la_pagina_de_entrada_sella_la_pestana(self):
        self.entrar()
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertTrue(r.context['sesion_nueva'])
        self.assertContains(r, 'rastrelital_pestana')

    def test_el_sello_es_de_un_solo_uso(self):
        """La segunda carga ya es "otra pestaña" mientras no muestre el sello."""
        self.entrar()
        self.client.get(reverse('tracking:dashboard'))
        r = self.client.get(reverse('tracking:dashboard'))
        self.assertFalse(r.context['sesion_nueva'])

    def test_el_mapa_tambien_sella_al_entrar_directo(self):
        """Con ?next=/mapa/ la página de entrada es el mapa, no el dashboard."""
        destino = reverse('tracking:fleet')
        self.client.post(self.login_url,
                         {'usuario': '1234', 'clave': '1234', 'next': destino})
        r = self.client.get(destino)
        self.assertTrue(r.context['sesion_nueva'])

    def test_el_login_no_lleva_guardia(self):
        """Vigilar la pestaña en el propio login sería un ciclo de cierres."""
        r = self.client.get(self.login_url)
        self.assertNotContains(r, 'rastrelital_pestana')

    def test_el_cierre_de_la_guardia_pasa_el_csrf(self):
        """El fetch del guardia manda el token por cabecera y debe ser aceptado.

        Si el CSRF lo rechazara, la sesión quedaría viva: el rebote al
        login volvería al dashboard, el guardia dispararía otra vez y la
        página entraría en un ciclo sin fin en vez de pedir la clave.
        """
        cliente = Client(enforce_csrf_checks=True)
        cliente.get(self.login_url)      # deja la cookie csrftoken
        cliente.post(self.login_url, {
            'usuario': '1234', 'clave': '1234',
            'csrfmiddlewaretoken': cliente.cookies['csrftoken'].value})
        self.assertTrue(cliente.session.get(CLAVE_SESION))

        r = cliente.post(reverse('tracking:logout'),
                         HTTP_X_CSRFTOKEN=cliente.cookies['csrftoken'].value)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(cliente.session.get(CLAVE_SESION))

    def test_la_cookie_muere_al_cerrar_el_navegador(self):
        """Sin fecha de vencimiento el navegador la borra al cerrarse.

        Es el respaldo de la guardia de pestaña para quien no ejecute
        JavaScript.
        """
        self.entrar()
        cookie = self.client.cookies['sessionid']
        self.assertEqual(cookie['max-age'], '')
        self.assertEqual(cookie['expires'], '')


class DashboardTests(TestCase):
    """La página del dashboard, ya con sesión iniciada."""

    def setUp(self):
        cache.clear()
        self.client.post(reverse('tracking:login'),
                         {'usuario': '1234', 'clave': '1234'})

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

    @patch.object(services.api_client, 'get_vehicles')
    def test_un_fallo_del_api_responde_502_y_no_revienta(self, vehiculos):
        vehiculos.side_effect = services.api_client.ApiError('el API se cayó')
        with self.assertLogs('tracking.views', level='ERROR'):
            r = self.client.get(reverse('tracking:api_dashboard'))
        self.assertEqual(r.status_code, 502)
        self.assertIn('error', r.json())
