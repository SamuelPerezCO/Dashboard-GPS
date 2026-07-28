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
    elementos = list(elementos)
    if not elementos:
        return []
    with ThreadPoolExecutor(max_workers=HILOS_CONSULTA) as pool:
        return list(pool.map(funcion, elementos))


def _hoy():
    return datetime.now().strftime('%Y-%m-%d')


def _norm_interno(interno):
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


def _nombre_geocerca(alerta):
    desc = html.unescape(alerta.get('Descripcion') or '')
    m = _RE_NOMBRE_GEOCERCA.search(desc)
    return m.group(1).strip() if m else ''


def empresa_de_geocerca(nombre):
    nombre = (nombre or '').strip()
    for empresa, patron in _PATRON_EMPRESA:
        if patron.search(nombre):
            return empresa
    return None


def tab_de_empresa(empresa):
    return empresa if empresa in EMPRESAS else TAB_SIN_IDENTIFICAR


def tabs_permitidas(empresas):
    if empresas is None:
        return list(EMPRESAS)
    elegidas = {str(e).strip().upper() for e in empresas}
    elegidas.add(TAB_SIN_IDENTIFICAR)
    return [e for e in EMPRESAS if e in elegidas]


def _filtro_de_tabs(empresa, permitidas):
    if empresa:
        return {empresa}
    if permitidas is None:
        return None
    return set(permitidas)


def fleet_summary():
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
    tipo = (alerta.get('TipoAlerta') or '').upper()
    status = (alerta.get('StatusAlerta') or '').upper()
    desc = (alerta.get('Descripcion') or '').upper()
    if 'FUERA' in status or 'FUERA DE LA GEOCERCA' in desc:
        return False
    return 'GEOCERCA' in tipo or 'GEOCERCA' in desc or 'DENTRO' in status


def _servicios_del_dia(fecha, es_hoy):
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
    for h, empresa in servicios_del_bus:
        if h >= hora:
            return empresa
    return servicios_del_bus[-1][1] if servicios_del_bus else None


def _timbradas_de_vehiculo(equipo, desde, hasta_efectivo, hoy,
                           primer_dia_mes, dentro_del_mes):
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
    d1 = datetime.strptime(desde, '%Y-%m-%d').date()
    d2 = datetime.strptime(hasta, '%Y-%m-%d').date()
    return [(d1 + timedelta(days=n)).isoformat() for n in range((d2 - d1).days + 1)]


def range_summary(desde=None, hasta=None, empresa=None, permitidas=None):
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
            servicios_bus_dia[(s['equipo'], d)].append((s['hora'], s['empresa']))
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
    hoy = _hoy()
    desde = (datetime.strptime(hoy, '%Y-%m-%d')
             - timedelta(days=DIAS_ULTIMO_MES)).strftime('%Y-%m-%d')
    return desde, hoy


def precalentar_ultimo_mes():
    desde, hasta = rango_ultimo_mes()
    range_summary(desde, hasta)
