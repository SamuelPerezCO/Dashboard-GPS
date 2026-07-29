"""Lógica del dashboard: servicios, timbradas y ocupación por bus.

Un servicio es una entrada a geocerca y una timbrada es un pasajero que
sube; la ocupación cruza las dos contra la capacidad del bus.
"""

import html
import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import api_client

logger = logging.getLogger(__name__)

HILOS_CONSULTA = 8


def _en_paralelo(funcion, elementos):
    """Aplica `funcion` a cada elemento con varios hilos, respetando el orden.

    El orden importa: quien llama empareja el resultado con la lista que
    mandó.
    """
    elementos = list(elementos)
    if not elementos:
        return []
    with ThreadPoolExecutor(max_workers=HILOS_CONSULTA) as pool:
        return list(pool.map(funcion, elementos))


def _hoy():
    """La fecha de hoy en YYYY-MM-DD, que es como habla el API."""
    return datetime.now().strftime('%Y-%m-%d')


def _norm_interno(interno):
    """Deja un interno comparable: 'int  7074' -> 'INT7074'."""
    return ''.join((interno or '').upper().split())


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


TAB_SIN_IDENTIFICAR = 'SIN-IDENTIFICAR'

EMPRESAS = ('PROCAPS', 'DITAR', 'RELIANZ', TAB_SIN_IDENTIFICAR)

ETIQUETA_EMPRESA = {
    'PROCAPS': 'PROCAPS',
    'DITAR': 'DITAR',
    'RELIANZ': 'RELIANZ',
    TAB_SIN_IDENTIFICAR: 'Sin identificar',
}

_PATRON_EMPRESA = (
    ('PROCAPS', re.compile(r'^(?:PROCAPS|RUTA\s*\d+)', re.IGNORECASE)),
    ('DITAR',   re.compile(r'\bDITAR\b', re.IGNORECASE)),
    ('RELIANZ', re.compile(r'\bRELIANZ\b', re.IGNORECASE)),
)

_RE_NOMBRE_GEOCERCA = re.compile(r'GEOCERCA\s+(.+?)\s+el\s+\d{4}/', re.IGNORECASE)


TURNOS = ('MANANA', 'TARDE', 'NOCHE')

ETIQUETA_TURNO = {
    'MANANA': 'Mañana (4:00 – 11:59)',
    'TARDE': 'Tarde (12:00 – 18:29)',
    'NOCHE': 'Noche (18:30 – 3:59)',
}

# Minuto del día en que arranca cada turno. La operación los nombra «4 a 11»,
# «12 a 6» y «6:30 a 3», que dejarían huecos entre uno y otro (11 a 12, 18:00
# a 18:30, 3 a 4); aquí cada turno se estira hasta donde empieza el siguiente
# para que las 24 horas queden cubiertas y los tres turnos sumen igual que
# «Todos». El de la noche cruza la medianoche: es el que se queda con la
# madrugada, hasta las 4.
_INICIO_TURNO = {
    'MANANA': 4 * 60,
    'TARDE': 12 * 60,
    'NOCHE': 18 * 60 + 30,
}

_RE_HORA = re.compile(r'^\s*(\d{1,2}):(\d{2})')


def turno_de_hora(hora):
    """Turno al que pertenece una hora 'HH:MM' o 'HH:MM:SS'.

    Returns:
        Uno de TURNOS, o None si la hora no viene con el formato esperado.
    """
    m = _RE_HORA.match(hora or '')
    if not m:
        return None
    minutos = int(m.group(1)) % 24 * 60 + int(m.group(2))
    if _INICIO_TURNO['MANANA'] <= minutos < _INICIO_TURNO['TARDE']:
        return 'MANANA'
    if _INICIO_TURNO['TARDE'] <= minutos < _INICIO_TURNO['NOCHE']:
        return 'TARDE'
    return 'NOCHE'


def _nombre_geocerca(alerta):
    """Saca el nombre de la geocerca del texto de la alerta.

    Returns:
        El nombre, o cadena vacía si la descripción no trae el patrón.
    """
    desc = html.unescape(alerta.get('Descripcion') or '')
    m = _RE_NOMBRE_GEOCERCA.search(desc)
    return m.group(1).strip() if m else ''


def empresa_de_geocerca(nombre):
    """Deduce la empresa por el nombre de la geocerca.

    Es la única señal que hay: el API no tiene campo de cliente. PROCAPS
    solo se reconoce al principio del nombre (o como «RUTA n»); DITAR y
    RELIANZ, en cualquier parte.

    Returns:
        La empresa, o None si el nombre no se parece a ninguna.
    """
    nombre = (nombre or '').strip()
    for empresa, patron in _PATRON_EMPRESA:
        if patron.search(nombre):
            return empresa
    return None


def tab_de_empresa(empresa):
    """Pestaña de una empresa; lo que no reconocemos cae en «Sin identificar»."""
    return empresa if empresa in EMPRESAS else TAB_SIN_IDENTIFICAR


def tabs_permitidas(empresas):
    """Pestañas que puede ver un usuario.

    Args:
        empresas: Empresas del usuario, o None si las ve todas.

    Returns:
        Las empresas permitidas, siempre con «Sin identificar» al final:
        ahí cae casi toda la actividad de DITAR y RELIANZ, y sin esa
        pestaña esos usuarios verían el dashboard vacío.
    """
    if empresas is None:
        return list(EMPRESAS)
    elegidas = {str(e).strip().upper() for e in empresas}
    elegidas.add(TAB_SIN_IDENTIFICAR)
    return [e for e in EMPRESAS if e in elegidas]


def _filtro_de_tabs(empresa, permitidas):
    """Pestañas que se van a contar.

    Returns:
        La pestaña pedida si hay una, el techo del usuario si no, o None
        cuando no hay nada que filtrar.
    """
    if empresa:
        return {empresa}
    if permitidas is None:
        return None
    return set(permitidas)


def fleet_summary():
    """Estado en vivo de toda la flota, para el mapa.

    Returns:
        Las unidades ordenadas por actividad —las que hoy no reportan van
        al final— y el conteo de encendidas, en movimiento y reportando.
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
    """Dice si la alerta es una entrada a geocerca.

    Las salidas se descartan: el viaje se cuenta cuando el bus entra, y
    contar también la salida lo duplicaría.
    """
    tipo = (alerta.get('TipoAlerta') or '').upper()
    status = (alerta.get('StatusAlerta') or '').upper()
    desc = (alerta.get('Descripcion') or '').upper()
    if 'FUERA' in status or 'FUERA DE LA GEOCERCA' in desc:
        return False
    return 'GEOCERCA' in tipo or 'GEOCERCA' in desc or 'DENTRO' in status


def _servicios_del_dia(fecha, es_hoy):
    """Servicios de un día, ordenados por hora y sin repetidos.

    El API manda la misma alerta varias veces, así que se descarta la que
    coincide en equipo, fecha, hora y descripción.

    Args:
        es_hoy: Acorta el cache a dos minutos, porque el día todavía crece.
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
    """Empresa a la que se le apunta una timbrada.

    La timbrada no dice de quién es, así que se le atribuye la del siguiente
    servicio de ese bus ese día, o la del último si ya no quedan.
    """
    for h, empresa in servicios_del_bus:
        if h >= hora:
            return empresa
    return servicios_del_bus[-1][1] if servicios_del_bus else None


def _timbradas_de_vehiculo(equipo, desde, hasta_efectivo, hoy,
                           primer_dia_mes, dentro_del_mes):
    """Timbradas de un bus en el rango, con el cache que más convenga.

    Si el rango cabe en el mes en curso se pide el mes entero y se recorta
    en memoria: así todos los rangos del mes comparten una sola consulta.

    Returns:
        Una tupla (timbradas, falló). Si el API falla devuelve lista vacía
        y el aviso, para que el dashboard pueda decir cuántas unidades se
        quedaron sin datos en vez de mostrar un cero limpio.
    """
    if not equipo or hasta_efectivo < desde:
        return [], False
    try:
        if dentro_del_mes:
            del_mes = api_client.get_passenger_events(
                equipo, primer_dia_mes, hoy, cache_ttl=1800)
            return [e for e in del_mes if desde <= e['fecha'] <= hasta_efectivo], False
        ttl = 600 if hasta_efectivo == hoy else 24 * 3600
        return api_client.get_passenger_events(
            equipo, desde, hasta_efectivo, cache_ttl=ttl), False
    except api_client.ApiError:
        logger.exception('No se pudieron leer las timbradas del equipo %s', equipo)
        return [], True


def _lista_dias(desde, hasta):
    """Todos los días del rango, con los dos extremos incluidos."""
    d1 = datetime.strptime(desde, '%Y-%m-%d').date()
    d2 = datetime.strptime(hasta, '%Y-%m-%d').date()
    return [(d1 + timedelta(days=n)).isoformat() for n in range((d2 - d1).days + 1)]


def range_summary(desde=None, hasta=None, empresa=None, permitidas=None, turno=None):
    """Ocupación del rango: por bus, por día y de la flota entera.

    La ocupación de un bus es timbradas / (servicios × capacidad): pasajeros
    promedio por viaje sobre los asientos que tiene. Los buses sin capacidad
    conocida o sin servicios quedan fuera del promedio en vez de entrar como
    cero y ensuciarlo.

    Args:
        desde, hasta: Rango en YYYY-MM-DD. Si vienen vacíos se consulta hoy,
            y si vienen al revés se voltean.
        empresa: Pestaña que se está viendo, o None para el total.
        permitidas: Techo del usuario. Aunque no pida una empresa, nunca se
            le suman viajes de una que no puede ver.
        turno: Franja horaria que se está viendo, o None para el día entero.
            Recorta servicios y timbradas por su hora, no por su fecha: lo que
            el turno de la noche registra después de medianoche se cuenta en
            el día calendario en que ocurrió, que es el siguiente.
    """
    hoy = _hoy()
    desde = desde or hoy
    hasta = hasta or hoy
    if desde > hasta:
        desde, hasta = hasta, desde
    dias = _lista_dias(desde, hasta)
    filtro = _filtro_de_tabs(empresa, permitidas)

    vehicles = api_client.get_vehicles()
    primer_dia_mes = hoy[:8] + '01'
    hasta_efectivo = min(hasta, hoy)
    rango_dentro_del_mes = desde >= primer_dia_mes

    dias_consultables = [d for d in dias if d <= hoy]
    servicios_por_dia = _en_paralelo(
        lambda d: _servicios_del_dia(d, d == hoy), dias_consultables)

    servicios = Counter()
    servicios_bus_dia = defaultdict(list)
    for d, servicios_del_dia in zip(dias_consultables, servicios_por_dia):
        for s in servicios_del_dia:
            # El índice por bus y día se arma con los servicios de todo el día,
            # aunque se esté viendo un turno: es lo que le pone empresa a cada
            # timbrada, y el servicio que se la pone bien puede ser del turno
            # de al lado.
            servicios_bus_dia[(s['equipo'], d)].append((s['hora'], s['empresa']))
            if turno and turno_de_hora(s['hora']) != turno:
                continue
            if filtro is None or tab_de_empresa(s['empresa']) in filtro:
                servicios[s['equipo']] += 1

    lecturas = _en_paralelo(
        lambda veh: _timbradas_de_vehiculo(
            veh.get('idgps'), desde, hasta_efectivo, hoy,
            primer_dia_mes, rango_dentro_del_mes),
        vehicles)

    vehiculos = []
    conteo_dia_interno = Counter()
    sin_empresa = 0
    unidades_con_error = 0
    for veh, (ev_rango, fallo) in zip(vehicles, lecturas):
        equipo = veh.get('idgps')
        interno = veh.get('nombre') or veh.get('patente') or ''
        if fallo:
            unidades_con_error += 1

        if turno:
            ev_rango = [e for e in ev_rango if turno_de_hora(e['hora']) == turno]

        emp_por_timbrada = [
            _empresa_de_timbrada(
                e['hora'], servicios_bus_dia.get((str(equipo), e['fecha']), []))
            for e in ev_rango
        ]
        sin_empresa += sum(1 for e in emp_por_timbrada if e is None)
        if filtro is not None:
            ev_rango = [e for e, emp in zip(ev_rango, emp_por_timbrada)
                        if tab_de_empresa(emp) in filtro]

        timbradas = len(ev_rango)
        n_servicios = servicios.get(str(equipo), 0)
        capacidad = CAPACIDAD_POR_INTERNO.get(_norm_interno(interno))
        if capacidad and n_servicios:
            ocupacion = round(timbradas / (n_servicios * capacidad) * 100, 2)
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
    ocupacion_flota = (round(sum(porcentajes) / len(porcentajes), 2)
                       if porcentajes else None)
    tabs = list(EMPRESAS) if permitidas is None else list(permitidas)
    return {
        'desde': desde,
        'hasta': hasta,
        'hoy': hoy,
        'empresa': empresa,
        'empresas': tabs,
        'etiquetas': {e: ETIQUETA_EMPRESA[e] for e in tabs},
        'turno': turno,
        'etiquetas_turno': dict(ETIQUETA_TURNO),
        'timbradas_inferidas': sin_empresa,
        'unidades_con_error': unidades_con_error,
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


DIAS_ULTIMO_MES = 30


def rango_ultimo_mes():
    """El último mes: de hace DIAS_ULTIMO_MES días hasta hoy."""
    hoy = _hoy()
    desde = (datetime.strptime(hoy, '%Y-%m-%d')
             - timedelta(days=DIAS_ULTIMO_MES)).strftime('%Y-%m-%d')
    return desde, hoy


def precalentar_ultimo_mes():
    """Consulta el último mes solo para dejarlo cacheado.

    Como las alertas se guardan día por día, le sirve a cualquier rango que
    caiga dentro, no solo al mes exacto.
    """
    desde, hasta = rango_ultimo_mes()
    range_summary(desde, hasta)
