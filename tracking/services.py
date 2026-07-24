"""Lógica de negocio del dashboard de flota.

Combina los datos crudos del API de Service24GPS
(:mod:`tracking.api_client`) para producir dos vistas:

* :func:`fleet_summary`: estado en vivo de toda la flota (posición,
  velocidad, ignición, etc.), usado por el mapa en tiempo real.
* :func:`range_summary`: ocupación de pasajeros por unidad y por
  empresa (PROCAPS o DITAR/RELIANZ) en un rango de fechas, inferida a
  partir de las alertas de entrada a geocerca y los eventos de
  timbrado (iButton) de cada unidad.
"""

import html
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import api_client


def _hoy():
    """Devuelve la fecha de hoy en formato ``YYYY-MM-DD``.

    Returns:
        La fecha actual del servidor como cadena.
    """
    return datetime.now().strftime('%Y-%m-%d')


def _norm_interno(interno):
    """Normaliza un número de interno para usarlo como clave de diccionario.

    Args:
        interno: Nombre o número de interno del vehículo, tal como viene
            del API (por ejemplo, ``'INT 7074'``).

    Returns:
        El interno en mayúsculas y sin espacios (por ejemplo, ``'INT7074'``).
        Devuelve cadena vacía si ``interno`` es None o vacío.
    """
    return ''.join((interno or '').upper().split())


# Capacidad de pasajeros por interno, tomada de la lista física de la flota.
_CAPACIDAD_CRUDA = {
    'INT 7074': 25, 'INT 7075': 25, 'INT 7076': 31, 'INT 7077': 37,
    'INT 7078': 37, 'INT 7079': 37, 'INT 7080': 37, 'INT 7088': 30,
    'INT 7091': 30, 'INT 7092': 30, 'INT 7093': 30, 'INT 7094': 30,
    'INT 7095': 30, 'INT 7097': 30, 'INT 7099': 30, 'INT 7202': 30,
    'INT 7203': 30, 'INT 7204': 30, 'INT 7227': 40, 'INT 7239': 40,
    'INT 7245': 40, 'INT 7248': 40, 'INT 7250': 40, 'INT 7269': 40,
    'INT 7273': 40, 'INT 7274': 40, 'INT 7275': 40, 'INT 7276': 40,
    'INT 7277': 40, 'INT 7278': 40,
}
CAPACIDAD_POR_INTERNO = {_norm_interno(k): v for k, v in _CAPACIDAD_CRUDA.items()}


# Empresas que se muestran como pestañas/filtros en el dashboard. DITAR y
# RELIANZ se agrupan bajo una sola pestaña porque comparten geocercas.
EMPRESAS = ('PROCAPS', 'DITAR-RELIANZ')

# Etiqueta legible para cada valor de EMPRESAS.
ETIQUETA_EMPRESA = {
    'PROCAPS': 'PROCAPS',
    'DITAR-RELIANZ': 'DITAR / RELIANZ',
}

# Patrones para inferir la empresa dueña de una geocerca a partir de su
# nombre. El API no expone un campo de "cliente": la única pista es el
# nombre de la geocerca (ver memoria "Empresa solo en nombre de geocerca").
_PATRON_EMPRESA = (
    ('PROCAPS', re.compile(r'^(?:PROCAPS|RUTA\s*\d+)', re.IGNORECASE)),
    ('DITAR',   re.compile(r'^DITAR', re.IGNORECASE)),
    ('RELIANZ', re.compile(r'^RELIANZ', re.IGNORECASE)),
)

# Extrae el nombre de la geocerca del texto de una alerta, con el formato
# "... GEOCERCA <nombre> el AAAA/...".
_RE_NOMBRE_GEOCERCA = re.compile(r'GEOCERCA\s+(.+?)\s+el\s+\d{4}/', re.IGNORECASE)


def _nombre_geocerca(alerta):
    """Extrae el nombre de la geocerca del texto de una alerta.

    Args:
        alerta: Diccionario de alerta devuelto por el API, con la clave
            ``Descripcion``.

    Returns:
        El nombre de la geocerca, sin espacios al inicio/final, o cadena
        vacía si el texto no coincide con el patrón esperado.
    """
    desc = html.unescape(alerta.get('Descripcion') or '')
    m = _RE_NOMBRE_GEOCERCA.search(desc)
    return m.group(1).strip() if m else ''


def empresa_de_geocerca(nombre):
    """Infiere la empresa dueña de una geocerca a partir de su nombre.

    Args:
        nombre: Nombre de la geocerca (ver :func:`_nombre_geocerca`).

    Returns:
        ``'PROCAPS'``, ``'DITAR'`` o ``'RELIANZ'`` según el patrón que
        coincida, o None si el nombre no corresponde a ninguna empresa
        conocida (por ejemplo, buses de la flota NIN sin geocerca; ver
        memoria "Flota NIN sin geocerca").
    """
    nombre = (nombre or '').strip()
    for empresa, patron in _PATRON_EMPRESA:
        if patron.match(nombre):
            return empresa
    return None


def tab_de_empresa(empresa):
    """Mapea una empresa concreta a su pestaña/filtro en el dashboard.

    DITAR y RELIANZ comparten una sola pestaña (``'DITAR-RELIANZ'``);
    cualquier otro valor cae en la pestaña de PROCAPS.

    Args:
        empresa: Valor devuelto por :func:`empresa_de_geocerca`
            (``'PROCAPS'``, ``'DITAR'``, ``'RELIANZ'`` o None).

    Returns:
        ``'PROCAPS'`` o ``'DITAR-RELIANZ'``.
    """
    return 'PROCAPS' if empresa == 'PROCAPS' else 'DITAR-RELIANZ'


def fleet_summary():
    """Construye el resumen en vivo de toda la flota para el mapa.

    Cruza el catálogo de vehículos con las posiciones/sensores en
    tiempo real, calculando además contadores agregados (unidades
    encendidas, en movimiento y que ya reportaron hoy).

    Returns:
        Diccionario con:

        * ``unidades``: lista de filas por unidad (posición, velocidad,
          ignición, conductor, etc.), ordenadas primero las que ya
          reportaron hoy y luego por velocidad descendente.
        * ``stats``: diccionario con los totales ``total``,
          ``encendidas``, ``en_movimiento`` y ``reportando_hoy``.
    """
    vehicles = {v.get('idgps'): v for v in api_client.get_vehicles()}
    units = api_client.get_live_data()
    hoy = _hoy()

    rows = []
    encendidas = movimiento = reportando_hoy = 0
    for u in units:
        veh = vehicles.get(u.get('GpsIdentif'), {})
        speed = float(u.get('GpsSpeed') or 0)
        ignition = str(u.get('Ignition')) == '1'
        report_date = u.get('ReportDate') or ''

        if ignition:
            encendidas += 1
        if speed > 0:
            movimiento += 1
        if report_date.startswith(hoy):
            reportando_hoy += 1

        rows.append({
            'unidad': u.get('UnitId'),
            'placa': u.get('UnitPlate'),
            'equipo': u.get('GpsIdentif'),
            'tipo': veh.get('tipo_vehiculo') or '',
            'marca': veh.get('marca') or '',
            'conductor': u.get('Conductor') or veh.get('conductor') or '',
            'lat': float(u['Latitude']) if u.get('Latitude') else None,
            'lng': float(u['Longitude']) if u.get('Longitude') else None,
            'velocidad': speed,
            'ignicion': ignition,
            'fecha_reporte': report_date,
            'reporto_hoy': report_date.startswith(hoy),
            'domicilio': u.get('Domicilio') or '',
            'bateria_veh': u.get('BateriaVeh'),
            'senal': u.get('Senal'),
        })

    rows.sort(key=lambda r: (not r['reporto_hoy'], -(r['velocidad'])))
    return {
        'unidades': rows,
        'stats': {
            'total': len(rows),
            'encendidas': encendidas,
            'en_movimiento': movimiento,
            'reportando_hoy': reportando_hoy,
        },
    }


def _es_entrada_geocerca(alerta):
    """Determina si una alerta representa una entrada a geocerca (un "servicio").

    Descarta explícitamente las alertas de salida ("fuera de la
    geocerca") para no contarlas como servicios.

    Args:
        alerta: Diccionario de alerta devuelto por el API, con las
            claves ``TipoAlerta``, ``StatusAlerta`` y ``Descripcion``.

    Returns:
        True si la alerta corresponde a una entrada a geocerca.
    """
    tipo = (alerta.get('TipoAlerta') or '').upper()
    status = (alerta.get('StatusAlerta') or '').upper()
    desc = (alerta.get('Descripcion') or '').upper()
    if 'FUERA' in status or 'FUERA DE LA GEOCERCA' in desc:
        return False
    return 'GEOCERCA' in tipo or 'GEOCERCA' in desc or 'DENTRO' in status


def _servicios_del_dia(fecha, es_hoy):
    """Obtiene la lista deduplicada de servicios (entradas a geocerca) de un día.

    Args:
        fecha: Fecha a consultar, en formato ``YYYY-MM-DD``.
        es_hoy: True si ``fecha`` es el día de hoy; controla el TTL del
            cache (más corto para hoy, ya que sigue cambiando).

    Returns:
        Lista de diccionarios con las claves ``equipo``, ``hora``,
        ``geocerca`` y ``empresa``, ordenada por hora ascendente.
    """
    ttl = 120 if es_hoy else 24 * 3600
    servicios = []
    vistos = set()
    for a in api_client.get_alerts(fecha, cache_ttl=ttl):
        key = (a.get('Equipo'), a.get('Fecha'), a.get('Hora'), a.get('Descripcion'))
        if key in vistos:
            continue
        vistos.add(key)
        if not _es_entrada_geocerca(a):
            continue
        geocerca = _nombre_geocerca(a)
        servicios.append({
            'equipo': str(a.get('Equipo')),
            'hora': a.get('Hora') or '',
            'geocerca': geocerca,
            'empresa': empresa_de_geocerca(geocerca),
        })
    servicios.sort(key=lambda s: s['hora'])
    return servicios


def _empresa_de_timbrada(hora, servicios_del_bus):
    """Asigna un timbrado de pasajero a la empresa del siguiente servicio.

    Un timbrado (evento de iButton) no indica por sí mismo a qué
    empresa pertenece; se le atribuye la empresa del primer servicio
    (entrada a geocerca) de ese bus cuya hora sea igual o posterior al
    timbrado. Si el timbrado ocurrió después del último servicio del
    día, se le atribuye la empresa de ese último servicio.

    Args:
        hora: Hora del timbrado (cadena comparable lexicográficamente
            con las horas de ``servicios_del_bus``).
        servicios_del_bus: Lista de tuplas ``(hora, empresa)`` de los
            servicios del bus en ese día, ordenada por hora ascendente.

    Returns:
        La empresa inferida, o None si el bus no tuvo servicios ese día.
    """
    for h, empresa in servicios_del_bus:
        if h >= hora:
            return empresa
    return servicios_del_bus[-1][1] if servicios_del_bus else None


def _lista_dias(desde, hasta):
    """Genera la lista de fechas (inclusive) entre dos fechas dadas.

    Args:
        desde: Fecha inicial, en formato ``YYYY-MM-DD``.
        hasta: Fecha final, en formato ``YYYY-MM-DD``.

    Returns:
        Lista de fechas en formato ``YYYY-MM-DD``, de ``desde`` a
        ``hasta`` inclusive.
    """
    d1 = datetime.strptime(desde, '%Y-%m-%d').date()
    d2 = datetime.strptime(hasta, '%Y-%m-%d').date()
    return [(d1 + timedelta(days=n)).isoformat() for n in range((d2 - d1).days + 1)]


def range_summary(desde=None, hasta=None, empresa=None):
    """Calcula la ocupación de pasajeros por vehículo en un rango de fechas.

    Para cada vehículo de la flota, cuenta cuántos servicios (entradas
    a geocerca) y cuántos timbrados de pasajero tuvo en el rango, y con
    eso estima el porcentaje de ocupación (timbradas / (servicios *
    capacidad)). Si se indica ``empresa``, todo se filtra a esa empresa
    usando la atribución de :func:`_empresa_de_timbrada`.

    Como optimización, cuando el rango cae dentro del mes en curso se
    reutiliza una sola consulta de eventos de todo el mes (cacheada por
    más tiempo) en vez de una consulta por rango.

    Args:
        desde: Fecha inicial del rango, en formato ``YYYY-MM-DD``. Si es
            None, se usa el día de hoy.
        hasta: Fecha final del rango, en formato ``YYYY-MM-DD``. Si es
            None, se usa el día de hoy.
        empresa: Uno de los valores de :data:`EMPRESAS` para filtrar el
            resultado a una sola empresa, o None para incluir todas.

    Returns:
        Diccionario con el rango normalizado (``desde``, ``hasta``,
        ``hoy``), la empresa filtrada, los catálogos ``empresas`` y
        ``etiquetas``, el conteo de timbradas sin empresa inferida
        (``timbradas_inferidas``), la ocupación promedio de la flota
        (``ocupacion_flota``), la lista de vehículos con sus métricas
        (``vehiculos``) y el detalle diario por interno para graficar
        (``detalle``).
    """
    hoy = _hoy()
    desde = desde or hoy
    hasta = hasta or hoy
    if desde > hasta:
        desde, hasta = hasta, desde
    dias = _lista_dias(desde, hasta)

    vehicles = api_client.get_vehicles()
    primer_dia_mes = hoy[:8] + '01'
    hasta_efectivo = min(hasta, hoy)
    rango_dentro_del_mes = desde >= primer_dia_mes

    servicios = Counter()
    servicios_bus_dia = defaultdict(list)
    for d in dias:
        if d > hoy:
            continue
        for s in _servicios_del_dia(d, d == hoy):
            servicios_bus_dia[(s['equipo'], d)].append((s['hora'], s['empresa']))
            if empresa is None or tab_de_empresa(s['empresa']) == empresa:
                servicios[s['equipo']] += 1

    vehiculos = []
    conteo_dia_interno = Counter()
    sin_empresa = 0
    for veh in vehicles:
        equipo = veh.get('idgps')
        interno = veh.get('nombre') or veh.get('patente') or ''

        if not equipo or hasta_efectivo < desde:
            ev_rango = []
        elif rango_dentro_del_mes:
            # El rango cabe en el mes actual: se pide el mes completo (una
            # sola consulta, cacheada más tiempo) y se recorta en memoria,
            # en vez de golpear el API con una consulta por cada rango.
            try:
                ev_mes = api_client.get_passenger_events(
                    equipo, primer_dia_mes, hoy, cache_ttl=1800)
            except api_client.ApiError:
                ev_mes = []
            ev_rango = [e for e in ev_mes if desde <= e['fecha'] <= hasta_efectivo]
        else:
            ttl = 600 if hasta_efectivo == hoy else 24 * 3600
            try:
                ev_rango = api_client.get_passenger_events(
                    equipo, desde, hasta_efectivo, cache_ttl=ttl)
            except api_client.ApiError:
                ev_rango = []

        emp_por_timbrada = [
            _empresa_de_timbrada(
                e['hora'], servicios_bus_dia.get((str(equipo), e['fecha']), []))
            for e in ev_rango
        ]
        sin_empresa += sum(1 for e in emp_por_timbrada if e is None)
        if empresa is not None:
            ev_rango = [e for e, emp in zip(ev_rango, emp_por_timbrada)
                        if tab_de_empresa(emp) == empresa]

        timbradas = len(ev_rango)
        n_servicios = servicios.get(str(equipo), 0)
        capacidad = CAPACIDAD_POR_INTERNO.get(_norm_interno(interno))
        if capacidad and n_servicios:
            # Ocupación = timbradas reales / cupo total (capacidad * viajes).
            ocupacion = round(timbradas / (n_servicios * capacidad) * 100)
        else:
            ocupacion = None
        capacidad_total = capacidad * n_servicios if capacidad and n_servicios else None
        vehiculos.append({
            'interno': interno,
            'equipo': equipo,
            'servicios': n_servicios,
            'timbradas': timbradas,
            'capacidad': capacidad,
            'ocupacion': ocupacion,
            'capacidad_total': capacidad_total,
        })

        for e in ev_rango:
            conteo_dia_interno[(e['fecha'], interno)] += 1

    vehiculos.sort(key=lambda v: v['interno'] or '')
    internos = [v['interno'] for v in vehiculos]
    porcentajes = [v['ocupacion'] for v in vehiculos if v['ocupacion'] is not None]
    ocupacion_flota = round(sum(porcentajes) / len(porcentajes)) if porcentajes else None
    return {
        'desde': desde,
        'hasta': hasta,
        'hoy': hoy,
        'empresa': empresa,
        'empresas': list(EMPRESAS),
        'etiquetas': dict(ETIQUETA_EMPRESA),
        'timbradas_inferidas': sin_empresa,
        'ocupacion_flota': ocupacion_flota,
        'vehiculos_en_promedio': len(porcentajes),
        'vehiculos': vehiculos,
        'detalle': {
            'internos': internos,
            'filas': [{'fecha': d,
                       'valores': [conteo_dia_interno.get((d, p), 0) for p in internos]}
                      for d in dias],
        },
    }
