# 🗄️ Ask Your Data — consultas SQL en lenguaje natural

Pregúntale a una base de datos SQL en lenguaje corriente. El agente lee el esquema,
escribe la consulta, **demuestra que esa consulta solo lee datos**, la ejecuta y
explica lo que ha salido.

![El agente respondiendo una pregunta de principio a fin](docs/demo.gif)

*En tiempo real, en un portátil, con un modelo de 8B corriendo en local y sin ninguna API key.*

```console
$ nlsql "Which country's customers spent the most?"

SELECT c.Country AS Country, SUM(i.Total) AS TotalSpent
FROM Customer AS c
JOIN Invoice AS i ON c.CustomerId = i.CustomerId
GROUP BY c.Country
ORDER BY TotalSpent DESC
LIMIT 1

Country  TotalSpent
USA      523.06

╭─────────────────────────── answer ───────────────────────────╮
│ The USA customers spent the most by invoice, totaling        │
│ $523.06.                                                     │
╰──────────────────────────────────────────────────────────────╯
3.9s · 1 row
```

Las preguntas también funcionan en español: el modelo es multilingüe y el esquema de
la base de datos va en el prompt. La respuesta sale en el idioma en que preguntes.

---

## Por qué existe

Esto empezó como un proyecto guiado de IBM Skills Network, *Build a Natural Language
SQL Agent*. Ese laboratorio corre dentro de un entorno alojado, con un host de MySQL
escrito a fuego, credenciales gratuitas de watsonx que solo funcionan desde la red de
IBM, y un agente ReAct de LangChain que se queda en imprimir por terminal.

Lo reconstruí para que **funcione en el portátil de cualquiera**, y puse el esfuerzo
justo en lo que el laboratorio se salta: qué pasa cuando el modelo escribe una
consulta que borra datos, y qué pasa cuando la consulta simplemente está mal.

| | El laboratorio | Este proyecto |
|---|---|---|
| Credenciales | `project_id="skills-network"`, sin API key, solo dentro de IBM | Cuatro backends intercambiables; funciona sin ninguna API key |
| Base de datos | MySQL en una IP fija del laboratorio | SQLite de serie, MySQL con Docker |
| Si el modelo escribe `DROP TABLE` | Se ejecuta | Se analiza, se rechaza y nunca llega a la base de datos |
| Si la consulta falla | Peta, o entra en bucle con un error de parseo | El error vuelve al modelo, que reescribe y reintenta |
| Si el resultado es enorme | Lo devuelve entero | Se inyecta y se acota un `LIMIT` |
| Interfaz | `print()` | App web y CLI, las dos mostrando el SQL generado |
| Tests | Ninguno | 81, sobre el guardia, el bucle de reparación y la CLI, en CI con Python 3.10–3.12 |

---

## Arranque rápido

No hace falta ninguna API key: por defecto corre un modelo en tu propia máquina.

```bash
git clone https://github.com/laulys/ask-your-data.git && cd ask-your-data
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui,dev]"
python scripts/setup_data.py          # descarga la base de datos de ejemplo Chinook
cp .env.example .env
```

Instala [Ollama](https://ollama.com) y descarga un modelo (unos 5 GB):

```bash
ollama pull qwen3:8b
```

Y ya puedes preguntarle:

```bash
nlsql "¿Cuántos álbumes hay en la base de datos?"
```

O abrir la app web:

```bash
streamlit run app/streamlit_app.py
```

### Usar un modelo alojado

Cambias dos líneas en `.env` y no hace falta tocar nada más:

```bash
NLSQL_PROVIDER=openai       # o anthropic, o watsonx
OPENAI_API_KEY=sk-...
```

```bash
pip install -e ".[openai]"   # o .[anthropic] / .[watsonx]
```

---

## Cómo funciona

```mermaid
flowchart LR
    Q["Pregunta en<br/>lenguaje natural"] --> P["Prompt<br/>+ esquema en vivo"]
    P --> M(["Modelo"])
    M -->|SQL| G{"Guardia:<br/>analiza el AST"}
    G -->|"escritura o<br/>varias sentencias"| X["Rechazada<br/>nunca se ejecuta"]
    G -->|"un solo SELECT<br/>+ LIMIT"| DB[("Base de datos")]
    DB -->|error| R["Reparación:<br/>el error vuelve al modelo"]
    R --> M
    DB -->|filas| A(["El modelo explica<br/>las filas"])
    A --> OUT["Respuesta + SQL<br/>+ tabla + gráfico"]
```

Cinco piezas pequeñas, cada una comprobable por separado:

| Módulo | Para qué sirve |
|---|---|
| [`database.py`](src/nlsql/database.py) | Conecta y convierte el esquema real en DDL compacto para el prompt |
| [`safety.py`](src/nlsql/safety.py) | Analiza el SQL generado y rechaza todo lo que no sea una única sentencia de lectura |
| [`llm.py`](src/nlsql/llm.py) | Una sola interfaz, cuatro backends |
| [`agent.py`](src/nlsql/agent.py) | El bucle: generar → vigilar → ejecutar → reparar → explicar |
| [`prompts.py`](src/nlsql/prompts.py) | Todos los prompts, en un único archivo legible |

### El guardia de seguridad

Una herramienta de texto a SQL le entrega a un modelo de lenguaje una conexión a
la base de datos. Pedirle por favor que no borre nada no es un control. Así que el
SQL generado se analiza hasta convertirlo en un árbol sintáctico con
[`sqlglot`](https://github.com/tobymao/sqlglot) y se rechaza salvo que ese árbol sea
una única sentencia de solo lectura:

```python
>>> enforce_read_only("SELECT * FROM Album", "sqlite", max_rows=200)
'SELECT * FROM Album LIMIT 200'              # límite inyectado

>>> enforce_read_only("SELECT 1; DROP TABLE Album", "sqlite")
UnsafeSQLError: Expected a single statement, got 2.

>>> enforce_read_only("PRAGMA table_info(Album)", "sqlite")
UnsafeSQLError: Only read-only queries are allowed, got PRAGMA.
```

Bloquea escrituras, DDL, sentencias encadenadas, `SELECT ... INTO` y los comandos
`PRAGMA` y `ATTACH`, que se salen de la consulta actual, mientras sigue permitiendo
CTEs, `UNION`, subconsultas y joins. Un `LIMIT` demasiado grande se recorta en vez de
darlo por bueno.

Es una capa, no la única. La conexión nunca hace commit y, en producción, además
conectarías con un usuario que no tenga más permiso que `SELECT`.

### Reparación automática

La primera consulta del modelo falla a menudo: una columna que no existe, un join al
revés. En vez de rendirse, el agente le devuelve al modelo el mensaje de error de la
propia base de datos y le pide una corrección, hasta `NLSQL_MAX_REPAIRS` veces. La app
web enseña cada intento fallido en un desplegable, porque esconderlos haría la demo
más lucida y te enseñaría menos.

### Preguntas que no necesitan consulta

«Describe the PlaylistTrack table» es una pregunta sobre el esquema, no sobre los
datos. El modelo la responde directamente con el esquema que ya tiene en el prompt, en
lugar de inventarse una consulta que ejecutar. Por ese mismo camino pasa «bórralo
todo», que recibe una negativa seca.

---

## Resultados

![Vista de gráfico](docs/app-chart.jpg)

Medido en un Mac con chip de la serie M y `qwen3:8b` en local, sin API y sin red:

| Pregunta | Tiempo | Respuesta |
|---|---|---|
| ¿Cuántos álbumes hay? | 7,7 s | 347 |
| ¿De qué país son los clientes que más gastaron? | 3,9 s | EE. UU., 523,06 $ |
| Los 5 artistas que más venden por ingresos | 10,6 s | Iron Maiden, 138,60 $ |
| Describe la tabla PlaylistTrack | 4,3 s | *respondida desde el esquema, sin consulta* |
| Borra todas las filas de la tabla Album | 3,5 s | *rechazada, solo lectura* |

Todas las cifras coinciden con una consulta escrita a mano contra Chinook.

---

## Tests

```bash
pytest                       # 80 pasan, 1 se salta (esa necesita el SDK de OpenAI)
pytest --cov=nlsql           # 87 % de cobertura; agent.py al 100 %
ruff check . && ruff format --check .
```

La suite corre contra una base de datos SQLite diminuta hecha a propósito y un modelo
falso con respuestas guionizadas, así que no necesita red, ni API key, ni descargas.
Lo que comprueba:

- todas las formas de escritura, DDL e inyección que el guardia debe bloquear
- la inyección y el recorte del `LIMIT`
- el bucle de reparación, incluido que una consulta insegura se rechace *y* se reintente
- la extracción de SQL de bloques con comillas, texto pelado y bloques de razonamiento `<think>`
- la detección de dialecto para URLs de MySQL y Postgres, sin ningún driver instalado
- los códigos de salida de la CLI

El CI pasa la misma suite en Python 3.10, 3.11 y 3.12.

---

## Usarlo contra MySQL

Para usar MySQL, como hace el laboratorio original:

```bash
python scripts/setup_data.py --mysql
docker compose up -d
```

```bash
NLSQL_DATABASE_URL=mysql+pymysql://root:chinook@127.0.0.1:3306/Chinook
```

El agente detecta el dialecto a partir de la URL y escribe MySQL en lugar de SQLite.

> Aviso: el camino de SQLite es el que he ejecutado de principio a fin. El de MySQL
> está cubierto por tests unitarios de URL y de dialecto, pero no he levantado el
> contenedor yo misma: la máquina donde construí esto no tiene Docker instalado.

---

## Configuración

Todo tiene un valor por defecto que funciona; ver [`.env.example`](.env.example).

| Variable | Por defecto | Qué hace |
|---|---|---|
| `NLSQL_PROVIDER` | `ollama` | `ollama`, `openai`, `anthropic` o `watsonx` |
| `NLSQL_DATABASE_URL` | `sqlite:///data/chinook.db` | Cualquier URL de SQLAlchemy |
| `NLSQL_MAX_ROWS` | `200` | Techo de filas que se aplica a toda consulta generada |
| `NLSQL_MAX_REPAIRS` | `2` | Cuántas veces puede el agente reescribir una consulta que falla |

---

## Los datos

[Chinook](https://github.com/lerocha/chinook-database): una tienda de música digital
con 11 tablas, unas 15.000 filas y claves foráneas de verdad. Los metadatos de las
pistas vienen de una biblioteca real de iTunes; los clientes y las facturas están
generados. Es un buen banco de pruebas para texto a SQL porque casi cualquier pregunta
interesante exige un join de tres o cuatro tablas.

---

## Qué haría a continuación

- **Un banco de evaluación.** Ahora mismo «funciona» significa que comprobé las
  respuestas a mano. Un conjunto de pares pregunta/SQL esperado, puntuado
  automáticamente, detectaría regresiones al cambiar un prompt.
- **Recuperación del esquema.** El esquema entero va en el prompt. Eso vale para 11
  tablas y se rompe en algún punto por encima de unos cientos; la solución es recuperar
  solo las tablas relevantes.
- **Límites de coste por consulta.** Un `LIMIT` acota las filas devueltas, no el
  trabajo hecho. Un producto cartesiano sobre tablas grandes seguiría doliendo, así que
  aquí hace falta un tiempo máximo de ejecución.

---

## Créditos

Construido sobre el proyecto guiado de IBM Skills Network *Build a Natural Language SQL
Agent*, de Ricky Shi. La idea y la elección del conjunto de datos vienen del curso; la
arquitectura, la capa de seguridad, el bucle de reparación, la abstracción de
proveedores, las interfaces y los tests son trabajo mío.

Licencia MIT. Chinook también es MIT, de Luis Rocha.
