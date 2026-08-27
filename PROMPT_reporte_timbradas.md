# Tarea: generar el Reporte de Timbradas desde el WebService, con nombres correctos

## Contexto

Este repo es el proyecto Django `Dashboard GPS` (`config/` + app `tracking/`). El
código está escrito en español — docstrings, comentarios e identificadores.
Mantén esa convención.

Hoy existe un archivo `Reporte_Timbradas_2026-08-01_a_2026-08-25.xlsx` en la raíz
del repo, pero **ningún código de este proyecto lo genera**. Está sin trackear en
git y se produjo fuera del codebase. El único uso de `openpyxl` es
`tracking/management/commands/import_flota_xlsx.py`, que *lee* el archivo de flota.

El reporte tiene 3 hojas:

- `Timbradas` — 8 columnas: Fecha, Hora, Nombre, RFID, Teléfono, Email, Activo, Domicilio
- `Resumen por unidad` — KPIs + una fila por unidad
- `Actividad por día` — una fila por día

Ojo con los nombres de columna de la plataforma, que son engañosos:
**`Activo` es el interno del bus** (`INT 7277`, `NIN673 INT 7306`) y
**`Domicilio` es la ruta** (`RUTA 9`, `DITAR`). No son lo que parecen.

## El defecto que hay que no repetir

La plataforma graba el nombre del pasajero **tal como estaba en el momento de cada
timbrada**. Una tarjeta RFID es un objeto físico: cuando alguien se va, la tarjeta
se le entrega a otra persona. Entonces una misma RFID legítimamente lleva dos
nombres distintos a lo largo de un mes.

El archivo que existe hoy aplanó eso a un solo nombre por tarjeta — el primero que
vio — y lo propagó hacia atrás y hacia adelante sobre todo el rango:

```python
# EL BUG. No lo reintroduzcas.
nombres = {}
for row in origen:
    nombres.setdefault(row.rfid, row.nombre)   # gana el primero
salida.nombre = nombres[row.rfid]              # mismo nombre en toda fecha
```

Impacto medido sobre 2026-08-01 → 2026-08-25: **41 filas mal atribuidas en 4
tarjetas** de 17 228 filas. Ejemplo: la tarjeta `011d7b8b00410023` timbró 1 vez
como JORGE LUIS MANJARRES (03-ago) y 20 veces como DINA LUZ SOCARRAS (04 al
25-ago); el archivo viejo acreditó las 21 a JORGE.

La relación *tarjeta → persona* tiene una dimensión temporal y un `dict` no la
tiene. Cualquier mapa `{rfid: nombre}` afirma en silencio "esto siempre fue así".
La resolución correcta es por `(rfid, fecha)`, o un registro con vigencias
(`valido_desde` / `valido_hasta`).

## Por qué no se puede resolver con el cliente actual

`tracking/api_client.py::get_passenger_events(equipo, fecha_ini, fecha_fin)`
llama a `historyGetEvents` con `idsEvents='2720'` y devuelve
`{fecha, hora, pasajero}`, donde `pasajero` es el iButton en hex extraído de
`datos_extras` con `_IBUTTON_RE`. **No trae nombre, ni teléfono, ni ruta.**

La buena noticia: ese hex es exactamente el mismo valor que la columna `RFID` del
reporte web (verificado: `01ab597600410001`, `0007756203`, etc. coinciden). O sea
que la llave del join ya existe. Lo que falta es el **padrón de pasajeros**
(RFID → nombre, teléfono, email) y, sobre todo, sus vigencias.

## Fase 1 — Descubrimiento (SOLO LECTURA, no escribas código todavía)

El reporte "Identificación de pasajero" existe en el sitio web de la plataforma,
así que algo del WebService lo alimenta. Encuéntralo.

1. Lee `tracking/api_client.py` completo para entender `call()`, `_post()`, el
   manejo de token y la convención de `cache_ttl` por endpoint.
2. Averigua qué acción del WebService devuelve el padrón de pasajeros / iButtons.
   Empieza por la documentación de Service24GPS si está disponible; si no, prueba
   nombres plausibles por el patrón de los que ya se usan (`vehicleGetAll`,
   `getdata`, `getAlerts`, `getProgrammedRoutesOnBus`, `historyGetEvents`).
3. Escribe un script **descartable** (en `/tmp`, no en el repo) que llame al
   candidato y vuelque el JSON crudo. Usa `python manage.py shell` para tener el
   `settings` cargado. **No imprimas ni escribas credenciales en ningún lado**;
   `.env` tiene `GPS_APIKEY`, `GPS_USERNAME`, `GPS_PASSWORD`, `GPS_API_BASE_URL`.

### La pregunta que decide todo el diseño

> **¿El endpoint del padrón devuelve solo el titular ACTUAL de cada tarjeta, o
> devuelve el historial con fechas de vigencia?**

Esto no es un detalle. Si solo devuelve el titular actual:

- construir el reporte desde el API **reintroduce exactamente el bug que estamos
  arreglando**, solo que peor — porque ahora el nombre equivocado sería el
  *último*, no el primero, y cambiaría solo cuando alguien vuelva a correr el
  reporte;
- en ese caso el `.xls` de la plataforma es la única fuente con verdad
  *point-in-time*, y el diseño correcto es ingerir ese archivo, no el API.

**Párate aquí y repórtame lo que encontraste antes de escribir código de
producción.** Incluye: el nombre de la acción, un ejemplo del JSON (con los datos
personales censurados), y tu respuesta a la pregunta de arriba con la evidencia
que la sostiene. Si no encuentras ningún endpoint de padrón, dilo claramente en
vez de inventar un rodeo.

## Fase 2 — Implementación (solo después de que yo apruebe la Fase 1)

Según lo que arroje la Fase 1:

- **Si hay vigencias en el API** → resuelve el nombre por `(rfid, fecha)` contra
  esas vigencias.
- **Si solo hay titular actual** → el comando ingiere el `.xls` de la plataforma
  y toma `Nombre` directo de cada fila. El API queda para completar lo que el
  `.xls` no trae.

En cualquiera de los dos casos:

- Nuevo comando: `tracking/management/commands/exportar_timbradas.py`, con
  argumentos `--desde`, `--hasta` y `--salida`.
- Extiende `tracking/api_client.py` con la función nueva siguiendo el estilo de
  las que ya están: docstring en español, `call(...)` con un `cache_ttl` elegido
  según qué tan rápido cambia el dato, y tolerancia a que el API devuelva un dict
  cuando hay un solo elemento (mira `get_alerts` como precedente).
- La lógica de agregación va en `tracking/services.py`, no en el comando. El
  comando solo orquesta y escribe el archivo.

### Reglas de negocio ya verificadas — respétalas

- `Pasajeros únicos` se cuenta por **RFID**, no por nombre. Confirmado a nivel
  global, por unidad y por día. Contar por nombre da 993 en vez de 994.
- `Promedio por día activo` = `round(timbradas / días con actividad)`.
- Las unidades sin actividad igual aparecen, con `0` y `—` en el resto.
- El orden de `Resumen por unidad` es timbradas descendente.
- El mapa `Interno → Equipo GPS` (IMEI) y las etiquetas de día en español
  (`sáb 01`, `mié 12`) **no vienen en el `.xls`** de la plataforma.
- Existen **19 filas exactamente duplicadas** (idénticas en las 8 columnas) que ya
  vienen así desde la plataforma. **No las dedupliques en silencio.** Agrega una
  bandera `--deduplicar` que por defecto esté apagada, y registra en el log
  cuántas se encontraron.

### Formato del archivo

Encabezado en negrita, texto `FFFFFFFF` sobre relleno `FF3F3F3F`, alineado a la
izquierda. Panel congelado y autofiltro en la hoja `Timbradas`. `RFID` y
`Teléfono` deben quedar como **texto**, si no Excel se come los ceros a la
izquierda (`0007756203` → `7756203`).

## Restricciones

- Sin dependencias nuevas. `openpyxl==3.1.5` ya está en `requirements.txt`.
- Nada de credenciales en el código, en los tests ni en el log.
- El comando no debe escribir en `db.sqlite3` salvo que lo justifiques primero.
- No toques los archivos `.xlsx` y `.pdf` que están sin trackear en la raíz.

## Tests

`tracking/tests.py` ya tiene ~880 líneas y mockea el API con `@patch`. Sigue ese
patrón. Como mínimo:

1. Una tarjeta reasignada a mitad de rango produce **dos nombres distintos**
   según la fecha. Este es el test de regresión del bug; escríbelo primero y
   confirma que falla con la implementación ingenua de `dict`.
2. `Pasajeros únicos` cuenta por RFID: dos nombres sobre la misma RFID siguen
   siendo 1 pasajero.
3. Las unidades sin timbradas aparecen con `0` y `—`.
4. Los ceros a la izquierda sobreviven al viaje de ida y vuelta por el `.xlsx`.
5. Las filas duplicadas se conservan por defecto y se eliminan con `--deduplicar`.

## Criterio de aceptación

Corriendo el comando para `2026-08-01 → 2026-08-25`, el resultado debe coincidir
con `Reporte_Timbradas_2026-08-01_a_2026-08-25.xlsx` (la versión ya corregida)
en las 17 228 filas, comparando como multiconjunto sobre las 8 columnas:

```python
import collections, openpyxl
def filas(p):
    ws = openpyxl.load_workbook(p, read_only=True)["Timbradas"]
    it = ws.iter_rows(min_row=2, values_only=True)
    return collections.Counter(
        tuple('' if v is None else str(v).strip() for v in r[:8]) for r in it if r[0])
a, b = filas("salida_nueva.xlsx"), filas("Reporte_Timbradas_2026-08-01_a_2026-08-25.xlsx")
assert not (a - b) and not (b - a), ((a-b), (b-a))
```

**No compares fila por fila.** El orden no es estable cuando varias timbradas
comparten `fecha + hora`; un diff posicional reporta cientos de falsos positivos
cuando las diferencias reales son cero.

`Reporte_Timbradas_2026-08-01_a_2026-08-25_backup.xlsx` es la versión **con** el
bug. Sirve como caso negativo: tu salida debe diferir de ella exactamente en 41
filas, todas solo en la columna `Nombre`.

## Entregable

Un PR con: la función nueva en `api_client.py`, la lógica en `services.py`, el
comando, los tests, y una nota corta en el `README.md` explicando por qué el
nombre se resuelve por fecha y no por tarjeta — para que nadie lo "simplifique"
a un `dict` más adelante.
