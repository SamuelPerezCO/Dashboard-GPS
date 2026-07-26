# Dashboard GPS — Rastrelital

Dashboard de ocupación de pasajeros para la flota de **Expreso Brasilia** (35
buses), construido sobre el WebService de **Service24GPS**.

Responde una pregunta: *¿qué tan llenos van los buses?* Cruza las entradas a
geocerca (los "servicios") con los timbrados de iButton (los pasajeros que
suben) y calcula la ocupación por vehículo, por empresa y por día.

---

## Cómo funciona

El API de Service24GPS **no tiene un campo de empresa o cliente**. La única
señal disponible es el nombre de la geocerca, que viene incrustado en el texto
de la alerta:

```
Unidad WEO 371 Generó ALERTA DENTRO DE GEOCERCA PROCAPS el 2026/07/21 21:31:19
```

De ahí sale todo el modelo:

| Concepto | De dónde sale |
|---|---|
| **Servicio** (un viaje) | Alerta de *entrada* a una geocerca. Las salidas se descartan. |
| **Empresa** | Nombre de la geocerca (`PROCAPS`, `DITAR`, `RELIANZ`). |
| **Timbrada** (un pasajero) | Evento `2720` del historial, con el `iButton_ID`. |
| **Capacidad** | Tabla fija por interno en `services.py` (lista física de la flota). |

La ocupación de un bus es:

```
ocupación % = timbradas / (servicios × capacidad) × 100
```

Es decir, pasajeros promedio por viaje ÷ asientos. Los buses sin capacidad
conocida o sin servicios registrados quedan fuera del promedio en vez de entrar
como cero, para no ensuciarlo.

Como una timbrada no dice a qué empresa pertenece, se le atribuye la del
**siguiente servicio** de ese mismo bus ese día.

### La pestaña «Sin identificar»

> **Hoy la única geocerca creada en la plataforma es la de `PROCAPS`**
> (verificado contra el API el 2026-07-25: 148 de 148 entradas en 7 días).

Por eso las pestañas de **DITAR** y **RELIANZ** salen en cero: no es un error
del código, es que sus geocercas no existen todavía. Toda la actividad que no
se puede atribuir cae en **Sin identificar**, y el dashboard lo explica en
pantalla para que un cero no se lea como "no trabajaron".

En cuanto alguien cree esas geocercas en la plataforma web, **ambas pestañas se
llenan solas**: el código ya reconoce `DITAR` y `RELIANZ` en cualquier parte del
nombre de la geocerca. No hay que tocar nada.

---

## Requisitos

- Python 3.14
- Credenciales del WebService de Service24GPS

## Instalación

```bash
python -m venv venv
venv\Scripts\activate           # Windows
pip install -r requirements.txt
python manage.py runserver
```

El dashboard queda en <http://localhost:8000/>.

## Configuración (`.env`)

El archivo `.env` va junto a `manage.py` y **nunca se sube a git**:

```ini
GPS_API_BASE_URL=https://api.service24gps.com/api/v1
GPS_APIKEY=...
GPS_USERNAME=...
GPS_PASSWORD=...

DJANGO_SECRET_KEY=...
DJANGO_DEBUG=1                  # 1 solo en desarrollo

DASHBOARD_USER=1234             # acceso al dashboard
DASHBOARD_PASSWORD=1234
```

> ⚠️ `DASHBOARD_USER` / `DASHBOARD_PASSWORD` traen `1234` por defecto para poder
> entrar de una vez. El sitio es público: **cámbialos en el `.env` de
> producción**. `DJANGO_DEBUG` por defecto está apagado, para que un servidor
> sin la variable no muestre el código y las credenciales al primer error.

## Acceso

El dashboard entero (páginas y endpoints JSON) exige iniciar sesión en
`/entrar/`. No hay usuarios en base de datos: se compara contra las dos
variables de arriba. Tras 5 intentos fallidos desde la misma IP el login se
bloquea 60 segundos. `/admin/` conserva su propio login de Django.

Mientras el usuario escribe su contraseña, el servidor **precalienta en
segundo plano** las consultas al API del último mes, para adelantar trabajo
antes de que la persona elija un rango.

## Pruebas

```bash
python manage.py test
```

47 pruebas que **no tocan la red**: el API se simula con `mock`, así que corren
sin credenciales y en un par de segundos.

---

## Rendimiento

Una consulta hace una petición por cada día del rango (alertas) más una por cada
bus (timbradas). Se lanzan **en paralelo** (8 a la vez), y todo se cachea.

El dashboard **abre sin fechas y no consulta nada solo**: las gráficas dicen
«Selecciona las fechas» y la consulta arranca cuando el usuario pone el rango
(o usa un atajo). Así no se gasta una consulta pesada en un rango que nadie
pidió.

El login precalienta el **último mes** (de hace 30 días a hoy, por ejemplo
25/06 → 25/07, ver `DIAS_ULTIMO_MES` en `services.py`) — medido contra el API
real:

| | Tiempo |
|---|---|
| Último mes en frío (lo que tarda el precalentamiento) | ~21 s |
| La misma consulta ya precalentada | ~0,4 s |

Como las alertas se cachean **día por día**, ese precalentamiento le sirve a
cualquier rango que caiga dentro del último mes, no solo al rango exacto.

Si el rango cabe en el mes en curso se pide el mes completo de una vez y se
recorta en memoria, en vez de una consulta distinta por cada rango.

Cuando el API falla para algunas unidades, el dashboard **avisa cuántas** en vez
de mostrar un cero limpio, porque un fallo se ve igual que "no llevó pasajeros".

---

## Estructura

```
config/            settings, urls y wsgi del proyecto Django
tracking/
  api_client.py    cliente del WebService (token, cache, reintentos)
  services.py      lógica de negocio: servicios, timbradas, ocupación
  views.py         páginas y endpoints JSON + login
  middleware.py    exige sesión iniciada en todo el sitio
  tests.py         pruebas (API simulado)
  templates/       base, dashboard, flota y acceso
```

El dashboard de mapa en vivo existe en `/mapa/` pero no está en el menú.

## Despliegue (Render)

El build solo necesita:

```bash
pip install -r requirements.txt
python manage.py collectstatic --no-input
```

**No hace falta `migrate`.** El dashboard no guarda nada en base de datos: los
datos vienen del API y la sesión del login viaja en una cookie firmada. El
disco de Render es efímero, así que cualquier cosa escrita en `db.sqlite3` se
perdería de todos modos.

Variables de entorno que hay que poner en Render:

| Variable | Por qué |
|---|---|
| `DJANGO_DEBUG` | **`0`.** Con `1`, cualquier error muestra el código, las variables locales y las credenciales a quien entre. |
| `DJANGO_SECRET_KEY` | Firma la cookie de sesión. Si falta, se usa la clave de ejemplo que está en el repo y **cualquiera podría fabricarse una sesión válida**. |
| `GPS_APIKEY`, `GPS_USERNAME`, `GPS_PASSWORD` | Acceso al WebService. |
| `DASHBOARD_USER`, `DASHBOARD_PASSWORD` | Acceso al dashboard (por defecto `1234`/`1234`). |

## Licencia

MIT
