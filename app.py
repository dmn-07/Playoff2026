import re
import io
import requests
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

# ============================================================
# CONFIGURACIÓN
# ============================================================

URL_CUARTA = "https://metrovoley.com.ar/tournaments/539"
URL_QUINTA = "https://metrovoley.com.ar/tournaments/540"

DATAPROJECT_BASE = "https://fmv-web.dataproject.com"

# Endpoints descubiertos/esperados para la segunda etapa.
# Se prueban antes de cualquier descubrimiento genérico.
# El scraper NUNCA acepta una tabla solo por ser la primera que encuentra:
# exige identificar explícitamente la etapa.
# URLs actuales conocidas de la web pública de FMV.
# Se usan como punto de partida; la app también descubre automáticamente
# las demás vistas de posiciones del torneo para no depender de IDs viejos.
KNOWN_STANDINGS = [
    "https://metrovoley.com.ar/tournaments/539/standings?group=5974&stage=2067",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    )
}

# Mock: 16 clubes reales del vóley argentino.
MOCK_CUARTA = [
    "Club Atlético Vélez Sarsfield",
    "Club Ciudad de Buenos Aires",
    "Universidad Nacional de La Matanza",
    "Club Atlético Boca Juniors",
    "Ferro Carril Oeste",
    "Club Atlético San Lorenzo de Almagro",
    "Club Gimnasia y Esgrima de Buenos Aires",
    "Club Italiano",
    "Club Atlético River Plate",
    "Club de Amigos",
    "Club Atlético Estudiantes de La Plata",
    "Club Atlético Lanús",
    "Club Atlético Defensores de Moreno",
    "Club Gimnasia y Esgrima de La Plata",
    "Club Atlético Huracán",
    "Club Atlético Independiente",
]

MOCK_QUINTA_A = [
    "Mock Quinta A1", "Mock Quinta A2", "Mock Quinta A3",
    "Mock Quinta A4", "Mock Quinta A5", "Mock Quinta A6",
]
MOCK_QUINTA_B = [
    "Mock Quinta B1", "Mock Quinta B2", "Mock Quinta B3",
    "Mock Quinta B4", "Mock Quinta B5", "Mock Quinta B6",
]

st.set_page_config(
    page_title="FMV Cuarta Masculino — Proyección Play Off",
    page_icon="🏐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# UTILIDADES
# ============================================================

def normalize(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def unique_keep_order(items):
    out = []
    seen = set()
    for x in items:
        x = normalize(x)
        key = x.lower()
        if x and key not in seen:
            seen.add(key)
            out.append(x)
    return out


def request_html(url, timeout=15):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text, r.url


def tables_from_html(html):
    try:
        return pd.read_html(io.StringIO(html))
    except Exception:
        return []


def find_team_column(df):
    candidates = [
        "Equipo", "Team", "Club", "Nombre", "Team Name",
        "Nombre del equipo", "Equipo / Club"
    ]
    for c in df.columns:
        if normalize(c) in candidates:
            return c

    # DataProject puede devolver MultiIndex o nombres compuestos.
    for c in df.columns:
        s = normalize(c).lower()
        if any(k in s for k in ["equipo", "team", "club"]):
            return c
    return None


def clean_team_name(value):
    s = normalize(value)
    s = re.sub(r"^Image:\s*", "", s, flags=re.I)
    # A veces DataProject duplica el acrónimo al extraer alt + texto.
    s = re.sub(r"\s+", " ", s)
    return s


def dataframe_to_standings(df):
    """Convierte una tabla HTML genérica en una tabla de posiciones usable."""
    df = df.copy()
    df.columns = [normalize(c) for c in df.columns]

    team_col = find_team_column(df)
    if team_col is None:
        return None

    # Buscar columna de posición.
    pos_col = None
    for c in df.columns:
        if normalize(c).lower() in {"pos", "pos.", "posición", "position", "rank", "#"}:
            pos_col = c
            break

    out = pd.DataFrame()
    if pos_col is not None:
        out["Pos"] = pd.to_numeric(df[pos_col], errors="coerce")
    else:
        out["Pos"] = range(1, len(df) + 1)

    out["Equipo"] = df[team_col].map(clean_team_name)

    # Conservar estadísticas disponibles.
    for wanted in ["PTS", "Puntos", "Clasificación por Puntos", "PJ", "PG", "PP"]:
        matches = [c for c in df.columns if normalize(c).lower() == wanted.lower()]
        if matches:
            out[wanted] = df[matches[0]]

    out = out[out["Equipo"].astype(str).str.len() > 0].copy()
    out["Pos"] = pd.to_numeric(out["Pos"], errors="coerce")
    out = out.dropna(subset=["Pos"]).sort_values("Pos").reset_index(drop=True)
    out["Pos"] = out["Pos"].astype(int)
    return out


def extract_standings_links(source_url, html, final_url):
    """Encuentra todas las vistas de posiciones del torneo actual."""
    links = []
    soup = BeautifulSoup(html, "html.parser")

    # Enlaces explícitos.
    for a in soup.find_all("a", href=True):
        href = urljoin(final_url, a["href"])
        if "/standings" in href.lower() and "tournaments/539" in href.lower():
            links.append(href)

    # También aparecen como opciones/selects o dentro del HTML embebido.
    for m in re.findall(r'https?://[^\"\'<> ]*tournaments/539/standings[^\"\'<> ]*', html):
        links.append(m.replace("&amp;", "&"))

    # Si la URL que nos dieron ya es una vista de posiciones, conservarla.
    if "/standings" in source_url.lower():
        links.append(source_url)

    return unique_keep_order(links)


def discover_standings_views(source_url):
    """Descubre las vistas de posiciones de la segunda etapa en FMV."""
    candidates = list(KNOWN_STANDINGS)
    try:
        html, final_url = request_html(source_url)
        candidates.extend(extract_standings_links(source_url, html, final_url))
    except Exception:
        pass

    # Probar la página de posiciones sin parámetros también: suele contener
    # los selectores de etapa/grupo aunque no estén como links normales.
    base = "https://metrovoley.com.ar/tournaments/539/standings"
    try:
        html, final_url = request_html(base)
        candidates.extend(extract_standings_links(base, html, final_url))
    except Exception:
        pass

    return unique_keep_order(candidates)


def scrape_standings_url(url):
    """Devuelve todas las tablas de posiciones válidas de una URL FMV."""
    html, final_url = request_html(url)
    results = []
    for df in tables_from_html(html):
        parsed = dataframe_to_standings(df)
        if parsed is not None and len(parsed) >= 4:
            results.append({"table": parsed, "url": final_url})
    return results


def scrape_second_stage(source_url):
    """
    Obtiene exclusivamente las tablas de la segunda etapa del torneo 539.

    La versión anterior dependía de IDs antiguos de DataProject y terminaba
    en Mock Data. Ahora parte de la web actual de FMV y descubre sus vistas.
    No acepta la Rueda Clasificación (15 partidos/16 equipos) como segunda etapa.
    """
    views = discover_standings_views(source_url)
    found = []

    for url in views:
        try:
            html, final_url = request_html(url)
            soup = BeautifulSoup(html, "html.parser")
            page_text = normalize(soup.get_text(" ", strip=True)).lower()
            parsed_tables = []
            for df in tables_from_html(html):
                parsed = dataframe_to_standings(df)
                if parsed is not None and len(parsed) >= 4:
                    parsed_tables.append(parsed)

            for parsed in parsed_tables:
                # La clasificación tiene 16 equipos. Las ruedas de segunda
                # etapa son las tablas pequeñas que aparecen después.
                if len(parsed) not in (7, 8):
                    continue

                found.append({
                    "table": parsed,
                    "url": final_url,
                    "text": page_text,
                })
        except Exception:
            continue

    # Elimina duplicados por conjunto de equipos.
    unique = []
    signatures = set()
    for item in found:
        sig = tuple(item["table"]["Equipo"].astype(str).str.upper().tolist())
        if sig not in signatures:
            signatures.add(sig)
            unique.append(item)

    # Identificación robusta por tamaño + nombres de etapa si FMV los expone.
    # Si solo aparece una tabla de 8, se toma como Campeonato porque la vista
    # actual conocida de stage=2067 es la Rueda Campeonato.
    campeonato = None
    reubic = None

    for item in unique:
        t = item["text"]
        if "rueda campeonato" in t:
            campeonato = item
        elif "rueda reubic" in t:
            reubic = item

    known = KNOWN_STANDINGS[0]
    for item in unique:
        if item["url"].split("?")[0] == known.split("?")[0] and "group=5974" in item["url"] and "stage=2067" in item["url"]:
            campeonato = campeonato or item

    if campeonato is None:
        # Elegir la tabla de 8 que tenga los equipos que actualmente figuran
        # arriba en la segunda fase; evita usar la tabla de clasificación.
        for item in unique:
            if len(item["table"]) == 8:
                campeonato = item
                break

    # La segunda tabla pequeña se considera Reubicación. Si la FMV publica
    # 7 equipos, se mantiene su numeración 9..15 y el puesto 16 queda vacío;
    # si publica 8, queda 9..16.
    for item in unique:
        if campeonato is not None and item is campeonato:
            continue
        if "rueda reubic" in item["text"] or len(item["table"]) in (7, 8):
            reubic = item
            break

    selected = {}
    if campeonato is not None:
        c = campeonato["table"].copy().sort_values("Pos").reset_index(drop=True)
        c["Pos"] = range(1, len(c) + 1)
        selected["Rueda Campeonato"] = {"table": c, "url": campeonato["url"]}

    if reubic is not None:
        r = reubic["table"].copy().sort_values("Pos").reset_index(drop=True)
        r["Pos"] = range(9, 9 + len(r))
        selected["Rueda Reubicación"] = {"table": r, "url": reubic["url"]}

    return selected

def make_mock_second_stage():
    # Se generan dos tablas de 8 para mantener la lógica del torneo.
    campeonato = MOCK_CUARTA[:8]
    reubic = MOCK_CUARTA[8:]
    df_c = pd.DataFrame({
        "Pos": range(1, 9),
        "Equipo": campeonato,
        "PTS": [40, 38, 36, 34, 32, 30, 28, 26],
    })
    df_r = pd.DataFrame({
        "Pos": range(9, 17),
        "Equipo": reubic,
        "PTS": [25, 24, 23, 22, 21, 20, 19, 18],
    })
    return df_c, df_r


def scrape_quinta(source_url):
    """
    Busca las tablas de Campeonato de Quinta.
    Como Quinta puede tener más de una zona, devuelve hasta los dos primeros
    equipos de cada tabla/zona que encuentre.
    """
    results = []

    candidates = []
    if "fmv-web.dataproject.com" in source_url:
        candidates = [source_url]
    else:
        try:
            candidates = get_dataproject_candidates(
                source_url, ["quinta", "campeonato", "standing", "standings"]
            )
        except Exception:
            candidates = []

    for url in candidates:
        try:
            html, final = request_html(url)
            soup = BeautifulSoup(html, "html.parser")
            page_text = normalize(soup.get_text(" ", strip=True))
            if "quinta" not in page_text.lower() and "quinta" not in final.lower():
                continue

            stage = classify_stage(page_text + " " + final)
            for df in tables_from_html(html):
                parsed = dataframe_to_standings(df)
                if parsed is not None and len(parsed) >= 2:
                    results.append({
                        "table": parsed,
                        "url": final,
                        "stage": stage,
                    })
        except Exception:
            continue

    return results


def mock_quinta():
    return [
        pd.DataFrame({
            "Pos": range(1, 7),
            "Equipo": MOCK_QUINTA_A,
            "PTS": [31, 29, 27, 25, 23, 21],
        }),
        pd.DataFrame({
            "Pos": range(1, 7),
            "Equipo": MOCK_QUINTA_B,
            "PTS": [30, 28, 26, 24, 22, 20],
        }),
    ]


# ============================================================
# CARGA DE DATOS
# ============================================================

def load_data(url_cuarta, url_quinta):
    errors = []

    second = scrape_second_stage(url_cuarta)
    if "Rueda Campeonato" in second and "Rueda Reubicación" in second:
        campeonato = second["Rueda Campeonato"]["table"].copy()
        reubic = second["Rueda Reubicación"]["table"].copy()
        source_c = "DataProject / FMV — segunda etapa validada"
    else:
        campeonato, reubic = make_mock_second_stage()
        source_c = "Mock Data"
        missing = []
        if "Rueda Campeonato" not in second:
            missing.append("Rueda Campeonato")
        if "Rueda Reubicación" not in second:
            missing.append("Rueda Reubicación")
        errors.append(
            "Blindaje activado: no se encontraron/validaron "
            + " y ".join(missing)
            + ". No se mezclaron datos de Rueda Clasificación; se usa Mock Data."
        )

    quinta_results = scrape_quinta(url_quinta)
    if len(quinta_results) >= 2:
        quinta_tables = [x["table"] for x in quinta_results[:4]]
        source_q = "DataProject / FMV — datos en vivo"
    elif len(quinta_results) == 1:
        quinta_tables = [quinta_results[0]["table"]]
        source_q = "DataProject — una tabla encontrada"
        errors.append(
            "Solo se encontró una tabla de Quinta; se completó con Mock Data."
        )
        quinta_tables.append(mock_quinta()[1])
    else:
        quinta_tables = mock_quinta()
        source_q = "Mock Data"
        errors.append(
            "No se pudieron localizar las tablas de Quinta en DataProject."
        )

    return campeonato, reubic, quinta_tables, source_c, source_q, errors


# ============================================================
# CUADROS
# ============================================================

def team_at(df, pos):
    row = df[df["Pos"] == pos]
    if row.empty:
        return f"Puesto {pos}"
    return clean_team_name(row.iloc[0]["Equipo"])


def fifth_candidates(tables):
    names = []
    for df in tables:
        if len(df) >= 2:
            names.append(team_at(df, 1))
            names.append(team_at(df, 2))
    names = unique_keep_order(names)
    if not names:
        return "1° Quinta"
    return " / ".join(names[:4])


def match_card(title, a, b, note=None):
    html = f"""
    <div class="match-card">
      <div class="match-title">{title}</div>
      <div class="team-row"><span class="seed">{a}</span></div>
      <div class="vs">VS</div>
      <div class="team-row"><span class="seed">{b}</span></div>
      {f'<div class="match-note">{note}</div>' if note else ''}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_playoff(campeonato, reubic, quinta):
    s2 = team_at(campeonato, 2)
    s3 = team_at(campeonato, 3)
    s4 = team_at(campeonato, 4)
    s5 = team_at(campeonato, 5)
    s6 = team_at(campeonato, 6)
    s7 = team_at(campeonato, 7)
    s8 = team_at(campeonato, 8)

    # Reubicación conserva numeración general 9-16.
    s9 = team_at(reubic, 9)
    s10 = team_at(reubic, 10)
    s11 = team_at(reubic, 11)
    s12 = team_at(reubic, 12)

    q1 = fifth_candidates(quinta)

    st.subheader("Octavos de Final")
    cols = st.columns(4)
    octavos = [
        ("Octavos 1", s9, s10, "Ganador → Cuartos 1 vs 2°"),
        ("Octavos 2", s6, q1, "Ganador → Cuartos 2 vs 5°"),
        ("Octavos 3", s8, s11, "Ganador → Cuartos 3 vs 3°"),
        ("Octavos 4", s7, s12, "Ganador → Cuartos 4 vs 4°"),
    ]
    for col, (title, a, b, note) in zip(cols, octavos):
        with col:
            match_card(title, a, b, note)

    st.markdown("### Cuartos de Final")
    cols = st.columns(4)
    cuartos = [
        ("Cuartos 1", s2, "Ganador Octavos 1"),
        ("Cuartos 2", s5, "Ganador Octavos 2"),
        ("Cuartos 3", s3, "Ganador Octavos 3"),
        ("Cuartos 4", s4, "Ganador Octavos 4"),
    ]
    for col, (title, a, b) in zip(cols, cuartos):
        with col:
            match_card(title, a, b)

    st.markdown("### Semifinales")
    cols = st.columns(2)
    with cols[0]:
        match_card("Semifinal 1", "Ganador Cuartos 1", "Ganador Cuartos 2")
    with cols[1]:
        match_card("Semifinal 2", "Ganador Cuartos 3", "Ganador Cuartos 4")

    st.markdown("### 🏆 Final — 2° Ascenso")
    match_card("Final", "Ganador Semifinal 1", "Ganador Semifinal 2")


def render_playout(reubic):
    s13 = team_at(reubic, 13)
    s14 = team_at(reubic, 14)
    s15 = team_at(reubic, 15)
    s16 = team_at(reubic, 16)

    st.subheader("Play Out — Permanencia / Descenso")
    c1, c2 = st.columns(2)
    with c1:
        match_card(
            "Partido A",
            s13,
            s16,
            "🔴 El perdedor desciende de categoría"
        )
    with c2:
        match_card(
            "Partido B",
            s14,
            s15,
            "🔴 El perdedor desciende de categoría"
        )


# ============================================================
# ESTILOS
# ============================================================

st.markdown("""
<style>
.main-title {
    font-size: 2.3rem;
    font-weight: 800;
    margin-bottom: .2rem;
}
.subtitle {
    color: #64748b;
    margin-bottom: 1rem;
}
.match-card {
    border: 1px solid #dbe3ef;
    border-radius: 14px;
    padding: 14px;
    margin-bottom: 16px;
    background: linear-gradient(180deg, #ffffff, #f8fafc);
    box-shadow: 0 3px 12px rgba(15,23,42,.06);
    min-height: 170px;
}
.match-title {
    font-weight: 800;
    font-size: 1rem;
    margin-bottom: 12px;
}
.team-row {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 9px;
    background: white;
    font-weight: 650;
}
.vs {
    text-align: center;
    font-size: .78rem;
    font-weight: 800;
    color: #64748b;
    padding: 5px 0;
}
.match-note {
    margin-top: 10px;
    font-size: .75rem;
    color: #64748b;
}
.source-ok {
    padding: 10px 14px;
    border-radius: 10px;
    background: #ecfdf5;
    border: 1px solid #a7f3d0;
}
.source-mock {
    padding: 10px 14px;
    border-radius: 10px;
    background: #fff7ed;
    border: 1px solid #fed7aa;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("⚙️ Configuración")
st.sidebar.caption("El scraper valida que las tablas sean de la segunda etapa.")
url_cuarta = st.sidebar.text_input("URL Cuarta División", URL_CUARTA)
url_quinta = st.sidebar.text_input("URL Quinta División", URL_QUINTA)

refresh = st.sidebar.button(
    "🔄 Actualizar Datos en Vivo",
    type="primary",
    use_container_width=True,
)

if refresh or "data_loaded" not in st.session_state:
    with st.spinner("Consultando FMV / DataProject..."):
        (
            campeonato,
            reubic,
            quinta_tables,
            source_c,
            source_q,
            errors,
        ) = load_data(url_cuarta, url_quinta)

    st.session_state.update({
        "data_loaded": True,
        "campeonato": campeonato,
        "reubic": reubic,
        "quinta": quinta_tables,
        "source_c": source_c,
        "source_q": source_q,
        "errors": errors,
    })

campeonato = st.session_state["campeonato"]
reubic = st.session_state["reubic"]
quinta_tables = st.session_state["quinta"]
source_c = st.session_state["source_c"]
source_q = st.session_state["source_q"]
errors = st.session_state["errors"]

# ============================================================
# UI PRINCIPAL
# ============================================================

st.markdown('<div class="main-title">🏐 FMV — Cuarta División Masculino</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Proyección automática del Play Off por el 2° Ascenso '
    'y Play Out a partir de las posiciones actuales.</div>',
    unsafe_allow_html=True,
)

if source_c == "DataProject / FMV — segunda etapa validada":
    st.markdown(
        '<div class="source-ok">🟢 Datos en vivo validados: '
        'Rueda Campeonato (1°–8°) + Rueda Reubicación (9°–16°). '
        'La Rueda Clasificación queda excluida.</div>',
        unsafe_allow_html=True,
    )

if source_c == "Mock Data":
    st.markdown(
        '<div class="source-mock">⚠️ Cuarta: se está usando Mock Data porque no '
        'se pudieron obtener las dos tablas de segunda etapa.</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="source-ok">🟢 Cuarta: posiciones obtenidas desde la fuente '
        'FMV / DataProject.</div>',
        unsafe_allow_html=True,
    )

for err in errors:
    st.warning(err)

tab1, tab2, tab3 = st.tabs([
    "📊 Posiciones",
    "🏆 Play Off — 2° Ascenso",
    "🚨 Play Out — Descenso",
])

with tab1:
    st.header("Segunda etapa — Cuarta División")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Rueda Campeonato")
        st.caption("Los puestos 1° al 8°. El 1° asciende directamente y queda fuera del Play Off.")
        st.dataframe(campeonato, use_container_width=True, hide_index=True)

    with c2:
        st.subheader("Rueda Reubicación")
        st.caption("Numeración general 9° al 16° para determinar Play Off y Play Out.")
        st.dataframe(reubic, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Quinta División — candidatos al 1° puesto")
    st.caption(
        "Se muestran los dos primeros de cada tabla encontrada. En el cuadro se "
        "mantienen como alternativas separadas por '/'."
    )

    qcols = st.columns(min(4, max(1, len(quinta_tables))))
    for i, (col, qdf) in enumerate(zip(qcols, quinta_tables)):
        with col:
            st.markdown(f"**Tabla/Zona {i+1}**")
            st.dataframe(qdf.head(5), use_container_width=True, hide_index=True)

with tab2:
    st.header("Cuadro proyectado — 2° Ascenso")
    st.info(
        "La llave se arma automáticamente con la clasificación actual. "
        "No se simulan resultados: los ganadores posteriores quedan como "
        "marcadores de posición."
    )
    render_playoff(campeonato, reubic, quinta_tables)

with tab3:
    render_playout(reubic)

st.divider()
st.caption(
    "Fuente configurada: Federación Metropolitana de Voleibol / DataProject. "
    "La app intenta obtener las etapas de segunda ronda y activa Mock Data si la "
    "fuente no responde o cambia su estructura."
)
