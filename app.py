import io
import re
import requests
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

# ============================================================
# FMV - CUARTA DIVISIÓN MASCULINO 2026
# Lee DIRECTAMENTE las dos tablas oficiales de Segunda Etapa.
# No reconstruye puntos ni consulta partidos individuales.
# ============================================================

URL_CUARTA = "https://metrovoley.com.ar/tournaments/539"
URL_CAMPEONATO = (
    "https://metrovoley.com.ar/tournaments/539/standings"
    "?stage=2067&group=5974&category=all"
)
URL_REUBICACION = (
    "https://metrovoley.com.ar/tournaments/539/standings"
    "?stage=2067&group=5975&category=all"
)
URL_QUINTA = "https://metrovoley.com.ar/tournaments/540"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    )
}

st.set_page_config(
    page_title="FMV Cuarta Masculino — Play Off",
    page_icon="🏐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# ESTILOS
# ============================================================

st.markdown("""
<style>
:root {
    --bordo: #800020;
    --verde: #14532D;
    --negro: #090909;
    --blanco: #ffffff;
    --gris: #f4f4f4;
}

[data-testid="stAppViewContainer"] { background: var(--blanco); }
[data-testid="stHeader"] { background: var(--negro) !important; }
[data-testid="stSidebar"] { background: var(--negro) !important; }
[data-testid="stSidebar"] * { color: var(--blanco) !important; }
[data-testid="stSidebar"] button {
    background: var(--bordo) !important;
    border: 1px solid var(--blanco) !important;
}

.main-title {
    border-left: 8px solid var(--bordo);
    padding-left: 14px;
    font-size: 2.2rem;
    font-weight: 800;
}

.subtitle { color: #555 !important; }

.team-card, .team-card * { color: #111827 !important; }

.match-card {
    border: 2px solid var(--verde);
    border-radius: 14px;
    padding: 14px;
    margin-bottom: 16px;
    background: #ffffff;
    box-shadow: 0 4px 14px rgba(0,0,0,.10);
    min-height: 170px;
}

.match-title { color: var(--bordo) !important; font-weight: 800; font-size: 1rem; }
.team-row {
    color: #111827 !important;
    border: 1px solid var(--bordo);
    border-radius: 8px;
    padding: 9px;
    background: #ffffff;
    font-weight: 650;
}
.team-row * { color: #111827 !important; }
.vs { text-align: center; font-size: .78rem; font-weight: 800; color: var(--verde); padding: 5px 0; }
.match-note { color: #333 !important; margin-top: 10px; font-size: .85rem; }

div[data-testid="stDataFrame"] { border: 2px solid var(--verde); border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# UTILIDADES
# ============================================================

def normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_team_name(value):
    text = normalize(value)
    text = re.sub(r"^Image:\s*", "", text, flags=re.I)
    return text


def request_html(url, timeout=25):
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def find_team_column(columns):
    for col in columns:
        name = normalize(col).lower()
        if name in {"equipo", "team", "club", "nombre"} or "equipo" in name:
            return col
    return None


def parse_standings(html):
    """Extrae la tabla oficial de posiciones de la URL recibida."""
    tables = []
    try:
        tables = pd.read_html(io.StringIO(html))
    except Exception:
        tables = []

    for raw in tables:
        df = raw.copy()
        df.columns = [normalize(c) for c in df.columns]
        team_col = find_team_column(df.columns)
        if team_col is None:
            continue

        pos_col = None
        pts_col = None
        for col in df.columns:
            low = normalize(col).lower()
            if low in {"pos", "pos.", "posición", "position", "rank", "#"}:
                pos_col = col
            if low in {"pts", "puntos", "puntos totales"} or low.startswith("pts"):
                pts_col = col

        if pts_col is None:
            continue

        out = pd.DataFrame()
        if pos_col is not None:
            out["Pos"] = pd.to_numeric(df[pos_col], errors="coerce")
        else:
            out["Pos"] = range(1, len(df) + 1)

        out["Equipo"] = df[team_col].map(clean_team_name)
        out["PTS"] = pd.to_numeric(df[pts_col], errors="coerce")

        # Estadísticas disponibles, sin calcularlas.
        for wanted in ["PG", "PJ", "PP", "DS", "SG", "SP", "DT", "TG", "TP"]:
            for col in df.columns:
                if normalize(col).upper() == wanted:
                    out[wanted] = pd.to_numeric(df[col], errors="coerce")
                    break

        out = out.dropna(subset=["Pos", "PTS"])
        out = out[out["Equipo"].astype(str).str.len() > 0]
        out["Pos"] = out["Pos"].astype(int)
        out = out.sort_values("Pos").reset_index(drop=True)

        # Las dos tablas de Segunda Etapa tienen 8 equipos.
        if len(out) == 8:
            return out

    # Fallback HTML directo por si pandas no interpreta la tabla.
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header_index = None
        header = []
        for i, row in enumerate(rows[:5]):
            cells = row.find_all(["th", "td"])
            candidate = [normalize(c.get_text(" ", strip=True)) for c in cells]
            low = [x.lower() for x in candidate]
            if any(x == "equipo" or "equipo" in x for x in low):
                header_index = i
                header = low
                break
        if header_index is None:
            continue

        team_idx = next((i for i, x in enumerate(header) if x == "equipo" or "equipo" in x), None)
        pts_idx = next((i for i, x in enumerate(header) if x in {"pts", "puntos", "puntos totales"} or x.startswith("pts")), None)
        if team_idx is None or pts_idx is None:
            continue

        data = []
        for row in rows[header_index + 1:]:
            cells = [normalize(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
            if len(cells) <= max(team_idx, pts_idx):
                continue
            pos = pd.to_numeric(cells[0], errors="coerce")
            pts = pd.to_numeric(cells[pts_idx], errors="coerce")
            if pd.isna(pos) or pd.isna(pts):
                continue
            data.append({"Pos": int(pos), "Equipo": clean_team_name(cells[team_idx]), "PTS": float(pts)})

        if len(data) == 8:
            return pd.DataFrame(data).sort_values("Pos").reset_index(drop=True)

    return None


def load_official_table(url):
    html = request_html(url)
    table = parse_standings(html)
    if table is None:
        raise ValueError("No se encontró una tabla oficial de 8 equipos en esta URL.")
    return table


@st.cache_data(ttl=300, show_spinner=False)
def load_second_stage():
    """Carga las dos URLs oficiales, sin reconstruir ningún dato."""
    campeonato = load_official_table(URL_CAMPEONATO)
    reubicacion = load_official_table(URL_REUBICACION)
    return campeonato, reubicacion


def team_at(df, pos):
    row = df[df["Pos"] == pos]
    if row.empty:
        return f"Puesto {pos}"
    return clean_team_name(row.iloc[0]["Equipo"])


def render_table(df):
    display = df.copy()
    cols = [c for c in ["Pos", "Equipo", "PTS", "PG", "PJ", "PP", "DS", "SG", "SP", "DT", "TG", "TP"] if c in display.columns]
    display = display[cols]
    st.dataframe(display, use_container_width=True, hide_index=True)


def match_card(title, a, b, note=None):
    note_html = f'<div class="match-note">{note}</div>' if note else ""
    st.markdown(
        f"""
        <div class="match-card">
            <div class="match-title">{title}</div>
            <div class="team-row">{a}</div>
            <div class="vs">VS</div>
            <div class="team-row">{b}</div>
            {note_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def quinta_placeholders():
    # El usuario pidió mantener los dos primeros como placeholders de Quinta.
    return "Equipo A", "Equipo B"


def render_playoff(campeonato, reubicacion):
    s2 = team_at(campeonato, 2)
    s3 = team_at(campeonato, 3)
    s4 = team_at(campeonato, 4)
    s5 = team_at(campeonato, 5)
    s6 = team_at(campeonato, 6)
    s7 = team_at(campeonato, 7)
    s8 = team_at(campeonato, 8)

    # La tabla de Reubicación tiene posiciones propias 1–8.
    # En el Play Off corresponden a los puestos generales 9–16:
    # Reubicación 1 = puesto general 9, ..., Reubicación 4 = puesto general 12.
    r1 = team_at(reubicacion, 1)
    r2 = team_at(reubicacion, 2)
    r3 = team_at(reubicacion, 3)
    r4 = team_at(reubicacion, 4)

    qta, qtb = quinta_placeholders()

    st.subheader("Octavos de Final")
    cols = st.columns(4)
    games = [
        ("Octavos 1", r1, r2, "Ganador → Cuartos 1"),
        ("Octavos 2", s6, f"1° Quinta: {qta} / {qtb}", "Ganador → Cuartos 2"),
        ("Octavos 3", s8, r3, "Ganador → Cuartos 3"),
        ("Octavos 4", s7, r4, "Ganador → Cuartos 4"),
    ]
    for col, game in zip(cols, games):
        with col:
            match_card(*game)

    st.subheader("Cuartos de Final")
    cols = st.columns(4)
    games = [
        ("Cuartos 1", s2, "Ganador Octavos 1"),
        ("Cuartos 2", s5, "Ganador Octavos 2"),
        ("Cuartos 3", s3, "Ganador Octavos 3"),
        ("Cuartos 4", s4, "Ganador Octavos 4"),
    ]
    for col, game in zip(cols, games):
        with col:
            match_card(*game)

    st.subheader("Semifinales")
    c1, c2 = st.columns(2)
    with c1:
        match_card("Semifinal 1", "Ganador Cuartos 1", "Ganador Cuartos 2")
    with c2:
        match_card("Semifinal 2", "Ganador Cuartos 3", "Ganador Cuartos 4")

    st.subheader("🏆 Final — 2° Ascenso")
    match_card("Final", "Ganador Semifinal 1", "Ganador Semifinal 2")


# ============================================================
# APP
# ============================================================

st.markdown('<div class="main-title">🏐 FMV Cuarta División Masculino</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Segunda Etapa — tablas oficiales y proyección del Play Off</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown("## FMV 2026")
    section = st.radio(
        "Segunda etapa",
        ["Campeonato", "Reubicación", "Play Off"],
        index=0,
    )
    st.caption("Datos de posiciones: FMV / Metrovoley")
    if st.button("🔄 Actualizar datos"):
        load_second_stage.clear()
        st.rerun()

try:
    with st.spinner("Leyendo las tablas oficiales de FMV..."):
        campeonato, reubicacion = load_second_stage()
except Exception as exc:
    st.error(f"No se pudieron leer las tablas oficiales de FMV: {exc}")
    st.info("Se están usando exclusivamente las URLs oficiales de Campeonato y Reubicación de Segunda Etapa; no se reconstruyen puntos.")
    st.stop()

if section == "Campeonato":
    st.header("Rueda Campeonato")
    st.caption(URL_CAMPEONATO)
    render_table(campeonato)
    st.success(f"1° Campeonato: {team_at(campeonato, 1)} — {campeonato.iloc[0]['PTS']} puntos")

elif section == "Reubicación":
    st.header("Rueda Reubicación")
    st.caption(URL_REUBICACION)
    render_table(reubicacion)
    st.success(f"1° Reubicación: {team_at(reubicacion, 9)} — {reubicacion.iloc[0]['PTS']} puntos")

else:
    st.header("Proyección Play Off — 2° Ascenso")
    st.caption("Los cruces se generan automáticamente a partir de las posiciones oficiales actuales.")
    render_playoff(campeonato, reubicacion)
