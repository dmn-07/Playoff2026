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

# Contraste: los nombres de equipos deben verse negros sobre las tarjetas claras.
st.markdown("""
<style>
.team-card, .team-card * { color: #111111 !important; }
.team-name, .team-name * { color: #111111 !important; }
div[data-testid="stMarkdownContainer"] .team-name { color: #111111 !important; }
</style>
""", unsafe_allow_html=True)

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


def extract_standings_links(source_url, html, final_url, tournament_id="539"):
    """
    Encuentra todas las vistas de posiciones.

    FMV tiene dos selectores en la página de Posiciones: uno para la etapa
    (p. ej. Primera/Segunda etapa) y otro para la rueda (Campeonato/Reubicación).
    Es importante leer los <select>/<option>, porque esas opciones no aparecen
    como enlaces <a> y por eso el scraper anterior nunca llegaba a Reubicación.
    """
    links = []
    soup = BeautifulSoup(html, "html.parser")
    standings_base = f"https://metrovoley.com.ar/tournaments/{tournament_id}/standings"

    for a in soup.find_all("a", href=True):
        href = urljoin(final_url, a["href"])
        if f"/tournaments/{tournament_id}/standings" in href.lower():
            links.append(href)

    # URLs absolutas y relativas embebidas en scripts/JSON.
    patterns = [
        rf'https?://[^"\'<> ]*/tournaments/{tournament_id}/standings[^"\'<> ]*',
        rf'/tournaments/{tournament_id}/standings[^"\'<> ]*',
    ]
    for pattern in patterns:
        for m in re.findall(pattern, html, flags=re.I):
            links.append(urljoin(final_url, m.replace("&amp;", "&")))

    # Capturar pares group/stage que ya estén escritos en HTML/JS.
    pair_patterns = [
        (r'[?&]group=(\d+)[^"\'<>]{0,220}[?&]stage=(\d+)', False),
        (r'[?&]stage=(\d+)[^"\'<>]{0,220}[?&]group=(\d+)', True),
        (r'"group"\s*:\s*"?(\d+)"?[^{}]{0,220}?"stage"\s*:\s*"?(\d+)"?', False),
        (r'"stage"\s*:\s*"?(\d+)"?[^{}]{0,220}?"group"\s*:\s*"?(\d+)"?', True),
    ]
    for pat, reversed_order in pair_patterns:
        for m in re.finditer(pat, html, flags=re.I):
            if reversed_order:
                stage, group = m.group(1), m.group(2)
            else:
                group, stage = m.group(1), m.group(2)
            links.append(f"{standings_base}?group={group}&stage={stage}")

    # ===== CLAVE: leer los dos selectores reales de FMV =====
    selects = []
    for sel in soup.find_all("select"):
        options = []
        for opt in sel.find_all("option"):
            value = normalize(opt.get("value"))
            label = normalize(opt.get_text(" ", strip=True))
            if value:
                options.append((value, label))
        if options:
            meta = " ".join([
                normalize(sel.get("name")),
                normalize(sel.get("id")),
                normalize(sel.get("class")),
                normalize(sel.get("data-param")),
                normalize(sel.get("data-name")),
            ]).lower()
            selects.append({"meta": meta, "options": options})

    # Identificar cuál selector es group y cuál stage usando los valores que
    # conocemos de la URL actual. Si el HTML no trae esos valores, también
    # usamos el nombre/id del selector.
    group_options = []
    stage_options = []
    for sel in selects:
        meta = sel["meta"]
        if "group" in meta or "rueda" in meta or "zona" in meta:
            group_options.extend(sel["options"])
        if "stage" in meta or "etapa" in meta or "fase" in meta:
            stage_options.extend(sel["options"])

    # Fallback por valores conocidos de la vista actual.
    if not group_options:
        for sel in selects:
            if any(v == "5974" for v, _ in sel["options"]):
                group_options = sel["options"]
                break
    if not stage_options:
        for sel in selects:
            if any(v == "2067" for v, _ in sel["options"]):
                stage_options = sel["options"]
                break

    # Fallback final: cualquier selector distinto del de group se considera
    # candidato a stage. Esto permite tolerar cambios menores del frontend.
    if group_options and not stage_options:
        for sel in selects:
            if sel["options"] is not group_options:
                stage_options.extend(sel["options"])

    current_stage = "2067"
    for value, label in stage_options:
        if value == "2067" or "segunda etapa" in label.lower():
            current_stage = value
            break

    # Construir específicamente las vistas de Segunda etapa / Campeonato y
    # Segunda etapa / Reubicación a partir de las opciones que muestra FMV.
    for value, label in group_options:
        low = label.lower()
        if "campeonato" in low or "reubic" in low:
            links.append(f"{standings_base}?group={value}&stage={current_stage}")

    # Si el selector no etiqueta claramente las ruedas, probar combinaciones
    # con la etapa actual. Luego el parser de tablas/etapas decide cuál es válida.
    if group_options:
        for value, label in group_options:
            if value != "5974":
                links.append(f"{standings_base}?group={value}&stage={current_stage}")

    if "/standings" in source_url.lower():
        links.append(source_url)
    return unique_keep_order(links)


def discover_standings_views(source_url, tournament_id="539", known=None):
    """Descubre vistas de posiciones, incluyendo las opciones de los selectores FMV."""
    candidates = list(known or [])

    # Hay que inspeccionar la vista conocida porque allí están los <select> que
    # contienen el ID de Reubicación aunque no exista un enlace <a> hacia ella.
    seed_urls = unique_keep_order(candidates + [source_url])
    base = f"https://metrovoley.com.ar/tournaments/{tournament_id}/standings"
    seed_urls.append(base)

    for seed in unique_keep_order(seed_urls):
        try:
            html, final_url = request_html(seed)
            candidates.extend(extract_standings_links(seed, html, final_url, tournament_id))
        except Exception:
            continue

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

# La Rueda Reubicación se obtiene de los equipos 9° a 16° de la clasificación.
# No se fijan nombres a mano: se derivan de la tabla de Campeonato (top 8) y
# del fixture de FMV, y luego se consultan las páginas individuales de cada club.
REUB_TEAMS_FALLBACK = [
    "ASTURIA", "EP B", "GEI B", "JUVA",
    "L.HERAS", "MUNMARG", "AFALP B", "UNLAM B",
]


def points_for_match(sets_for, sets_against):
    if sets_for > sets_against:
        return 3 if sets_against <= 1 else 2
    if sets_against > sets_for and sets_for == 2:
        return 1
    return 0


def _find_team_score(text, team):
    """Devuelve el resultado de sets inmediatamente posterior al nombre del equipo."""
    pattern = r"(?<![A-Z0-9])" + re.escape(team) + r"(?![A-Z0-9])"
    matches = list(re.finditer(pattern, text, flags=re.I))
    if not matches:
        return None
    # En las tarjetas de FMV el nombre participante aparece seguido por el
    # resultado de sets (0-3). Tomamos la primera cifra 0..3 posterior.
    tail = text[matches[-1].end():matches[-1].end() + 80]
    m = re.search(r"\b([0-3])\b", tail)
    return int(m.group(1)) if m else None


def _team_page_links(source_url, teams):
    """Encuentra la página /teams/.../matches de cada club desde el fixture."""
    fixture_url = source_url.rstrip("/") + "/fixture"
    html, final_url = request_html(fixture_url, timeout=20)
    soup = BeautifulSoup(html, "html.parser")
    wanted = {normalize(t).upper(): t for t in teams}
    links = {}

    for a in soup.find_all("a", href=True):
        href = urljoin(final_url, a["href"])
        if "/teams/" not in href or "/matches" not in href:
            continue
        label = clean_team_name(a.get_text(" ", strip=True))
        if not label:
            continue
        label_up = label.upper()
        for upper, original in wanted.items():
            if upper == label_up or upper in label_up or label_up in upper:
                links[original] = href

    # Si el fixture no expone todos los enlaces, intentar descubrirlos desde
    # el HTML completo buscando /teams/<id>/matches cerca del nombre del club.
    for m in re.finditer(r'href=["\']([^"\']*/teams/\d+/matches)["\']', html, re.I):
        href = urljoin(final_url, m.group(1))
        before = BeautifulSoup(html[max(0, m.start()-500):m.start()+500], "html.parser").get_text(" ", strip=True)
        for upper, original in wanted.items():
            if upper in before.upper() and original not in links:
                links[original] = href

    return links


def _parse_team_matches(team, team_url, all_teams):
    """Lee partidos finalizados de un equipo, usando el ID único del partido."""
    html, _ = request_html(team_url, timeout=20)
    soup = BeautifulSoup(html, "html.parser")
    all_names = list(dict.fromkeys(all_teams))
    out = []

    for a in soup.find_all("a", href=True):
        href = urljoin(team_url, a["href"])
        if "/matches/" not in href:
            continue
        txt = normalize(a.get_text(" ", strip=True))
        low = txt.lower()
        if "reubicacion" in low:
            phase = "reubic"
        elif "clasificacion" in low:
            phase = "class"
        else:
            continue

        # Un partido sin resultado todavía no debe entrar en la tabla.
        team_occ = list(re.finditer(r"(?<![A-Z0-9])" + re.escape(team) + r"(?![A-Z0-9])", txt, re.I))
        if not team_occ:
            continue
        team_pos = team_occ[-1]

        candidates = []
        for other in all_names:
            if other.upper() == team.upper():
                continue
            occ = list(re.finditer(r"(?<![A-Z0-9])" + re.escape(other) + r"(?![A-Z0-9])", txt, re.I))
            if occ:
                candidates.append((other, occ[-1]))
        if not candidates:
            continue

        rival, rival_pos = min(candidates, key=lambda x: abs(x[1].start() - team_pos.start()))
        sf = _find_team_score(txt, team)
        sa = _find_team_score(txt, rival)
        if sf is None or sa is None or sf == sa or sf > 3 or sa > 3:
            continue

        # El ID del partido es la clave real: evita duplicados sin importar
        # desde qué página de equipo se haya leído.
        match_id = re.search(r"/matches/(\d+)", href)
        match_id = match_id.group(1) if match_id else href
        out.append({
            "id": match_id,
            "phase": phase,
            "team": team,
            "rival": rival,
            "sf": sf,
            "sa": sa,
        })

    return out


def scrape_reubic_from_fixture(source_url, reub_teams, all_teams=None):
    """Calcula Reubicación para TODOS los equipos con el mismo arrastre del 50%."""
    teams = list(reub_teams)
    all_teams = list(all_teams or teams)
    links = _team_page_links(source_url, all_teams)

    stats = {
        t: {
            "class_pts": 0.0,
            "stage_pts": 0.0,
            "PG": 0,
            "PJ": 0,
            "PP": 0,
            "SG": 0,
            "SP": 0,
            "TG": 0,
            "TP": 0,
        }
        for t in teams
    }
    seen_matches = set()

    for team in all_teams:
        url = links.get(team)
        if not url:
            continue
        try:
            matches = _parse_team_matches(team, url, all_teams)
        except Exception:
            continue

        for m in matches:
            if m["id"] in seen_matches:
                continue
            seen_matches.add(m["id"])

            a_name = m["team"]
            b_name = m["rival"]
            if a_name not in stats and b_name not in stats:
                # Partido entre dos equipos de Campeonato, irrelevante para
                # el arrastre de Reubicación.
                continue

            # Clasificación: solo importa el resultado de cada equipo de la
            # futura Reubicación contra cualquier rival.
            if m["phase"] == "class":
                if a_name in stats:
                    stats[a_name]["class_pts"] += points_for_match(m["sf"], m["sa"])
                if b_name in stats:
                    stats[b_name]["class_pts"] += points_for_match(m["sa"], m["sf"])
                continue

            # Segunda etapa: solo partidos entre los 8 equipos de Reubicación.
            if m["phase"] != "reubic" or a_name not in stats or b_name not in stats:
                continue

            a = stats[a_name]
            b = stats[b_name]
            sf, sa = m["sf"], m["sa"]
            a["PJ"] += 1
            b["PJ"] += 1
            a["SG"] += sf
            a["SP"] += sa
            b["SG"] += sa
            b["SP"] += sf
            p_a = points_for_match(sf, sa)
            p_b = points_for_match(sa, sf)
            a["stage_pts"] += p_a
            b["stage_pts"] += p_b
            if sf > sa:
                a["PG"] += 1
                b["PP"] += 1
            else:
                b["PG"] += 1
                a["PP"] += 1

    # No devolvemos datos parciales: la tabla debe tener los 8 equipos.
    # Cada uno recibe exactamente la misma regla: 50% de sus puntos de
    # Clasificación + puntos obtenidos en la Rueda Reubicación.
    rows = []
    for team, x in stats.items():
        carry = x["class_pts"] * 0.5
        total = carry + x["stage_pts"]
        rows.append({
            "Pos": 0,
            "Equipo": team,
            "PTS": round(total, 1),
            "Arrastre 50%": round(carry, 1),
            "PTS Reubicación": round(x["stage_pts"], 1),
            "PG": x["PG"],
            "PJ": x["PJ"],
            "PP": x["PP"],
            "DS": x["SG"] - x["SP"],
            "SG": x["SG"],
            "SP": x["SP"],
        })

    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["PTS", "DS", "SG"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    df["Pos"] = range(9, 9 + len(df))
    return df[[
        "Pos", "Equipo", "PTS", "Arrastre 50%", "PTS Reubicación",
        "PG", "PJ", "PP", "DS", "SG", "SP"
    ]]


def scrape_second_stage(source_url):
    """Obtiene Campeonato y calcula Reubicación con la misma regla para los 8."""
    campeonato = None
    try:
        html, final_url = request_html(KNOWN_STANDINGS[0], timeout=20)
        for df in tables_from_html(html):
            parsed = dataframe_to_standings(df)
            if parsed is not None and len(parsed) == 8:
                parsed = parsed.sort_values("Pos").reset_index(drop=True)
                parsed["Pos"] = range(1, 9)
                campeonato = {"table": parsed, "url": final_url}
                break
    except Exception:
        pass

    if campeonato is None:
        return {}

    # Los 8 que no están en Campeonato se obtienen del conjunto oficial de
    # clubes de Cuarta; el fallback solo identifica equipos, nunca puntos.
    all_teams = list(dict.fromkeys(
        list(campeonato["table"]["Equipo"]) + REUB_TEAMS_FALLBACK
    ))
    top8 = {normalize(x).upper() for x in campeonato["table"]["Equipo"]}
    reub_teams = [x for x in REUB_TEAMS_FALLBACK if x.upper() not in top8]
    if len(reub_teams) != 8:
        return {"Rueda Campeonato": campeonato}

    rebuilt = scrape_reubic_from_fixture(source_url, reub_teams, all_teams)
    return {
        "Rueda Campeonato": campeonato,
        "Rueda Reubicación": {
            "table": rebuilt,
            "url": source_url.rstrip("/") + "/fixture",
        },
    }

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
    """Obtiene las tablas actuales de Quinta desde la web pública de FMV."""
    results = []
    views = discover_standings_views(
        source_url,
        "540",
        ["https://metrovoley.com.ar/tournaments/540/standings"],
    )
    for url in views:
        try:
            html, final_url = request_html(url)
            for df in tables_from_html(html):
                parsed = dataframe_to_standings(df)
                if parsed is not None and len(parsed) >= 2:
                    results.append({"table": parsed, "url": final_url, "stage": "FMV"})
        except Exception:
            continue

    # Deduplicar tablas idénticas.
    out, seen = [], set()
    for item in results:
        sig = tuple(item["table"]["Equipo"].astype(str).str.upper().tolist())
        if sig not in seen:
            seen.add(sig)
            out.append(item)
    return out

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

    # Importante: cada rueda se conserva de forma independiente. Antes, si
    # faltaba Reubicación, se reemplazaban también los datos reales de Campeonato
    # por Mock Data, que era exactamente lo que aparecía en el teléfono.
    if "Rueda Campeonato" in second:
        campeonato = second["Rueda Campeonato"]["table"].copy()
        source_c = "FMV — Campeonato en vivo"
    else:
        campeonato = pd.DataFrame({"Pos": pd.Series(dtype="int"), "Equipo": pd.Series(dtype="str")})
        source_c = "No encontrada"
        errors.append("No se pudo obtener Rueda Campeonato desde FMV.")

    if "Rueda Reubicación" in second:
        reubic = second["Rueda Reubicación"]["table"].copy()
        source_r = "FMV — Reubicación en vivo"
    else:
        reubic = pd.DataFrame({"Pos": pd.Series(dtype="int"), "Equipo": pd.Series(dtype="str")})
        source_r = "No encontrada"
        errors.append("No se pudo calcular la Rueda Reubicación completa desde los resultados de FMV.")

    quinta_results = scrape_quinta(url_quinta)
    if quinta_results:
        quinta_tables = [x["table"] for x in quinta_results[:4]]
        source_q = "FMV — datos en vivo"
    else:
        quinta_tables = []
        source_q = "No encontrada"
        errors.append("No se pudieron localizar las tablas de Quinta en la web pública de FMV.")

    return campeonato, reubic, quinta_tables, source_c, source_r, source_q, errors


# ============================================================
# CUADROS
# ============================================================

def team_at(df, pos):
    if df is None or df.empty or "Pos" not in df.columns or "Equipo" not in df.columns:
        return f"Puesto {pos} (pendiente FMV)"
    row = df[df["Pos"] == pos]
    if row.empty:
        return f"Puesto {pos} (pendiente FMV)"
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
.match-title, .match-title * {
    color: #111827 !important;
}
.match-title {
    font-weight: 800;
    font-size: 1rem;
    margin-bottom: 12px;
}
.team-row, .team-row * {
    color: #111827 !important;
}
.team-row, .team-row p, .team-row span, .team-row div {
    color: #111827 !important;
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
    color: #475569 !important;
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
div[data-testid="stAlert"] * { color: #111827 !important; }
.stDataFrame, .stDataFrame * { color: #111827 !important; }
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

if refresh or "data_loaded" not in st.session_state or "source_r" not in st.session_state:
    with st.spinner("Consultando FMV / DataProject..."):
        (
            campeonato,
            reubic,
            quinta_tables,
            source_c,
            source_r,
            source_q,
            errors,
        ) = load_data(url_cuarta, url_quinta)

    st.session_state.update({
        "data_loaded": True,
        "campeonato": campeonato,
        "reubic": reubic,
        "quinta": quinta_tables,
        "source_c": source_c,
        "source_r": source_r,
        "source_q": source_q,
        "errors": errors,
    })

campeonato = st.session_state["campeonato"]
reubic = st.session_state["reubic"]
quinta_tables = st.session_state["quinta"]
source_c = st.session_state["source_c"]
source_r = st.session_state["source_r"]
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

if source_c == "FMV — Campeonato en vivo" and not reubic.empty:
    st.markdown(
        '<div class="source-ok">🟢 Datos en vivo validados de la segunda etapa de FMV. '
        'La Rueda Clasificación queda excluida.</div>',
        unsafe_allow_html=True,
    )

if source_c == "FMV — Campeonato en vivo":
    st.markdown(
        '<div class="source-ok">🟢 Cuarta: Campeonato obtenido en vivo desde FMV. '
        'La Reubicación se muestra solo cuando FMV la publica/detecta.</div>',
        unsafe_allow_html=True,
    )

for err in errors:
    st.info(err)

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
        st.caption("Numeración general 9° al 16°. PTS = 50% de la Clasificación + puntos de la Rueda Reubicación, aplicado por igual a los 8 equipos.")
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
    if reubic.empty:
        st.info("La Rueda Reubicación todavía no fue detectada desde FMV; por eso no se inventan equipos en el Play Out.")
    else:
        render_playout(reubic)

st.divider()
st.caption(
    "Fuente: Federación Metropolitana de Voleibol. La app prioriza datos reales y "
    "no reemplaza una rueda válida por Mock Data si falta otra tabla."
)
