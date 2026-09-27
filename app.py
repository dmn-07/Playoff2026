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
:root {
    --bordo: #800020;
    --bordo-dark: #5c0017;
    --verde: #14532d;
    --verde-dark: #0b3b1f;
    --negro: #090909;
    --blanco: #ffffff;
    --gris: #f4f4f4;
}

/* Identidad visual del club */
[data-testid="stAppViewContainer"] {
    background: var(--blanco);
}
[data-testid="stHeader"] {
    background: var(--negro) !important;
}
[data-testid="stSidebar"] {
    background: var(--negro) !important;
}
[data-testid="stSidebar"] * {
    color: var(--blanco) !important;
}
[data-testid="stSidebar"] button {
    background: var(--bordo) !important;
    border: 1px solid var(--blanco) !important;
}

.team-card, .team-card * { color: #111111 !important; }
.team-name, .team-name * { color: #111111 !important; }
div[data-testid="stMarkdownContainer"] .team-name { color: #111111 !important; }

div[data-testid="stMetric"] {
    background: var(--blanco) !important;
    border: 2px solid var(--verde) !important;
    border-radius: 12px !important;
    padding: 10px 14px !important;
}
div[data-testid="stMetric"] * { color: #111827 !important; }
.stAlert, div[data-testid="stAlert"] {
    background: #fafafa !important;
    border: 1px solid var(--verde) !important;
    color: #111827 !important;
}
div[data-testid="stAlert"] p, div[data-testid="stAlert"] span, div[data-testid="stAlert"] div {
    color: #111827 !important;
}

.main-title {
    border-left: 8px solid var(--bordo);
    padding-left: 14px;
}
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


def _plain(text):
    """Texto comparable sin tildes, útil para etiquetas del frontend."""
    import unicodedata
    s = normalize(text).lower()
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(ch)
    )


def _numeric_id(value):
    """Devuelve un ID numérico si value es un entero/str entero."""
    try:
        n = int(str(value).strip())
        return n if n > 0 else None
    except Exception:
        return None


def _ids_near_label(html, label_words):
    """Busca IDs de group/stage cerca de una etiqueta del frontend.

    SportsFlow/FM​V puede entregar los selectores como JSON/RSC en vez de
    <option>. Por eso no dependemos de una estructura HTML concreta.
    """
    found = []
    text = html.replace('\\"', '"').replace('\\/', '/')
    low = _plain(text)

    for word in label_words:
        target = _plain(word)
        pos = 0
        while True:
            idx = low.find(target, pos)
            if idx < 0:
                break
            # Una ventana amplia pero local: suele contener label + value/id.
            lo = max(0, idx - 1800)
            hi = min(len(text), idx + 1800)
            chunk = text[lo:hi]

            group_ids = []
            stage_ids = []
            # Variantes habituales de JSON/props/URL.
            patterns_group = [
                r'(?i)["\'](?:group|groupId|group_id|groupID)["\']\s*[:=]\s*["\']?(\d+)',
                r'(?i)[?&]group=(\d+)',
                r'(?i)["\'](?:value|id)["\']\s*[:=]\s*["\']?(\d+)'
            ]
            patterns_stage = [
                r'(?i)["\'](?:stage|stageId|stage_id|stageID)["\']\s*[:=]\s*["\']?(\d+)',
                r'(?i)[?&]stage=(\d+)'
            ]
            for pat in patterns_group:
                group_ids += [int(x) for x in re.findall(pat, chunk)]
            for pat in patterns_stage:
                stage_ids += [int(x) for x in re.findall(pat, chunk)]

            # Evitar capturar IDs enormes/irrelevantes.
            group_ids = [x for x in group_ids if 1 <= x <= 100000]
            stage_ids = [x for x in stage_ids if 1 <= x <= 100000]
            for gid in group_ids:
                for sid in (stage_ids or [2067]):
                    found.append((gid, sid))
            pos = idx + len(target)

    return found


def _extract_json_objects_from_scripts(soup):
    """Devuelve objetos JSON encontrados en scripts sin asumir Next.js/React."""
    objects = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        if not raw or len(raw) > 2_000_000:
            continue
        raw = raw.strip()
        if not raw:
            continue
        # JSON puro.
        if raw.startswith("{") or raw.startswith("["):
            try:
                import json
                objects.append(json.loads(raw))
            except Exception:
                pass
    return objects


def _walk_labelled_ids(obj, inherited=None):
    """Busca pares group/stage en objetos JSON donde aparece una rueda."""
    import json
    found = []
    inherited = dict(inherited or {})

    if isinstance(obj, dict):
        local = dict(inherited)
        for k, v in obj.items():
            kl = _plain(k)
            if kl in {"group", "groupid", "group_id", "groupid"}:
                n = _numeric_id(v)
                if n is not None:
                    local["group"] = n
            elif kl in {"stage", "stageid", "stage_id", "stageid"}:
                n = _numeric_id(v)
                if n is not None:
                    local["stage"] = n

        values_text = " ".join(
            str(v) for v in obj.values()
            if isinstance(v, (str, int, float))
        )
        label = _plain(values_text)
        if "reubic" in label or "campeonato" in label:
            if local.get("group"):
                found.append((local["group"], local.get("stage", 2067), label[:180]))

        for v in obj.values():
            found.extend(_walk_labelled_ids(v, local))

    elif isinstance(obj, list):
        for v in obj:
            found.extend(_walk_labelled_ids(v, inherited))

    return found


def extract_standings_links(source_url, html, final_url, tournament_id="539"):
    """Descubre las vistas oficiales de Posiciones de Segunda Etapa.

    IMPORTANTE: no reconstruye resultados. Busca las dos vistas que FMV ya
    publica (Rueda Campeonato y Rueda Reubicación) y devuelve sus URLs.
    """
    links = []
    soup = BeautifulSoup(html, "html.parser")
    standings_base = f"https://metrovoley.com.ar/tournaments/{tournament_id}/standings"

    # 1) Enlaces normales.
    for a in soup.find_all("a", href=True):
        href = urljoin(final_url, a["href"])
        if f"/tournaments/{tournament_id}/standings" in href.lower():
            links.append(href)

    # 2) URLs embebidas en HTML/JS/RSC.
    for m in re.findall(
        rf"(?:https?:)?//[^\"'<> ]*/tournaments/{tournament_id}/standings[^\"'<> ]*|"
        rf"/tournaments/{tournament_id}/standings[^\"'<> ]*",
        html,
        flags=re.I,
    ):
        links.append(urljoin(final_url, m.replace("&amp;", "&")))

    # 3) <select>/<option>, si el servidor los entrega.
    options = []
    for sel in soup.find_all("select"):
        for opt in sel.find_all("option"):
            value = normalize(opt.get("value"))
            label = normalize(opt.get_text(" ", strip=True))
            if value:
                options.append((value, label))

    stage = None
    for value, label in options:
        if "segunda etapa" in _plain(label):
            stage = value
            break
    if stage is None:
        parsed = urlparse(final_url)
        stage = parse_qs(parsed.query).get("stage", ["2067"])[0]

    for value, label in options:
        low = _plain(label)
        if "campeonato" in low or "reubic" in low:
            # Si value ya es una URL, respetarla; si es un ID, usar group.
            if "/standings" in value:
                links.append(urljoin(final_url, value))
            elif _numeric_id(value):
                links.append(f"{standings_base}?group={value}&stage={stage}")

    # 4) El frontend puede guardar las opciones en JSON/RSC.
    for gid, sid in _ids_near_label(html, ["Rueda Reubicación", "Rueda Reubicacion", "Reubicación", "Reubicacion"]):
        links.append(f"{standings_base}?group={gid}&stage={sid}")
    for gid, sid in _ids_near_label(html, ["Rueda Campeonato", "Campeonato"]):
        links.append(f"{standings_base}?group={gid}&stage={sid}")

    # 5) JSON puro dentro de <script>.
    for obj in _extract_json_objects_from_scripts(soup):
        for gid, sid, _label in _walk_labelled_ids(obj):
            links.append(f"{standings_base}?group={gid}&stage={sid}")

    # 6) Cualquier pareja explícita group/stage del HTML.
    for m in re.finditer(r"[?&]group=(\d+)[^\"'<>]{0,1200}[?&]stage=(\d+)", html, flags=re.I):
        links.append(f"{standings_base}?group={m.group(1)}&stage={m.group(2)}")
    for m in re.finditer(r"[?&]stage=(\d+)[^\"'<>]{0,1200}[?&]group=(\d+)", html, flags=re.I):
        links.append(f"{standings_base}?group={m.group(2)}&stage={m.group(1)}")

    if "/standings" in source_url.lower():
        links.append(source_url)
    return unique_keep_order(links)


def discover_standings_views(source_url, tournament_id="539", known=None):
    """Descubre todas las vistas de Posiciones del torneo."""
    candidates = list(known or [])
    base = f"https://metrovoley.com.ar/tournaments/{tournament_id}/standings"
    seed_urls = unique_keep_order(candidates + [source_url, base])

    for seed in seed_urls:
        try:
            html, final_url = request_html(seed, timeout=20)
            candidates.extend(extract_standings_links(seed, html, final_url, tournament_id))
        except Exception:
            continue

    return unique_keep_order(candidates)

def scrape_standings_url(url):
    """Lee una tabla de posiciones FMV de forma robusta.

    Primero usa pandas y, si el HTML de FMV no se deja interpretar bien,
    hace un parseo directo de las filas <tr>.
    """
    html, final_url = request_html(url)
    results = []

    # 1) Parser habitual.
    for df in tables_from_html(html):
        parsed = dataframe_to_standings(df)
        if parsed is not None and len(parsed) >= 4:
            results.append({"table": parsed, "url": final_url})

    if results:
        return results

    # 2) Fallback directo para cambios en el HTML de FMV.
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header = [normalize(c.get_text(" ", strip=True)).lower()
                  for c in rows[0].find_all(["th", "td"])]
        if not any(h in {"equipo", "team", "club"} or "equipo" in h for h in header):
            # Buscar una fila de encabezado dentro de las primeras filas.
            header = []
            header_index = None
            for idx, row in enumerate(rows[:4]):
                hs = [normalize(c.get_text(" ", strip=True)).lower()
                      for c in row.find_all(["th", "td"])]
                if any("equipo" in h or h in {"team", "club"} for h in hs):
                    header, header_index = hs, idx
                    break
            if header_index is None:
                continue
        else:
            header_index = 0

        team_idx = next((i for i, h in enumerate(header)
                         if "equipo" in h or h in {"team", "club"}), None)
        pts_idx = next((i for i, h in enumerate(header)
                        if h in {"pts", "puntos", "puntos totales"} or h.startswith("pts")), None)
        if team_idx is None or pts_idx is None:
            continue

        data = []
        for row in rows[header_index + 1:]:
            cells = row.find_all(["th", "td"])
            texts = [normalize(c.get_text(" ", strip=True)) for c in cells]
            if len(texts) <= max(team_idx, pts_idx):
                continue
            team = clean_team_name(texts[team_idx])
            if not team:
                continue
            pos = pd.to_numeric(texts[0], errors="coerce")
            pts = pd.to_numeric(texts[pts_idx], errors="coerce")
            if pd.isna(pos) or pd.isna(pts):
                continue
            row_out = {"Pos": int(pos), "Equipo": team, "PTS": float(pts)}
            # Copiar el resto de columnas conocidas cuando existan.
            for i, name in enumerate(header):
                if i >= len(texts):
                    continue
                if name.upper() in {"PG", "PJ", "PP", "DS", "SG", "SP", "DT", "TG", "TP"}:
                    row_out[name.upper()] = pd.to_numeric(texts[i], errors="coerce")
            data.append(row_out)

        if len(data) >= 4:
            parsed = pd.DataFrame(data).sort_values("Pos").reset_index(drop=True)
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
    """Descubre las páginas /teams/.../matches de todos los clubes.

    FMV no siempre publica los enlaces de los equipos directamente en el
    fixture. En ese caso el fixture sí contiene enlaces a los partidos; cada
    página de partido contiene los enlaces de los dos equipos. Usamos esos
    enlaces como puente para descubrir los 8 equipos de Reubicación.
    """
    fixture_url = source_url.rstrip("/") + "/fixture"
    seed_urls = [fixture_url, "https://metrovoley.com.ar/teams/5531"]  # MUNMARG
    wanted = {normalize(t).upper(): t for t in teams}
    links = {}

    def register_team_link(label, href):
        label_up = clean_team_name(label).upper()
        if not label_up:
            return
        # FMV puede mostrar "ASTURIA A", "MUNMARG A", etc.; los nombres
        # usados por la tabla son "ASTURIA", "MUNMARG", etc.
        for upper, original in wanted.items():
            if (
                label_up == upper
                or label_up.startswith(upper + " ")
                or upper in label_up
            ):
                links[original] = href.rstrip("/") + "/matches"
                return

    for page_url in seed_urls:
        try:
            html, final_url = request_html(page_url, timeout=20)
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")

        # Camino 1: enlaces de equipos que estén directamente en la página.
        for a in soup.find_all("a", href=True):
            href = urljoin(final_url, a["href"])
            if "/teams/" in href:
                register_team_link(a.get_text(" ", strip=True), href)

        # Camino 2: el fixture suele tener /matches/<id>, pero no /teams/<id>.
        # Abrimos una muestra de partidos para descubrir los enlaces de equipos.
        match_urls = []
        for a in soup.find_all("a", href=True):
            href = urljoin(final_url, a["href"])
            if "/matches/" in href and href not in match_urls:
                match_urls.append(href)
            if len(match_urls) >= 32:
                break

        for match_url in match_urls:
            try:
                match_html, match_final = request_html(match_url, timeout=15)
            except Exception:
                continue
            msoup = BeautifulSoup(match_html, "html.parser")
            for a in msoup.find_all("a", href=True):
                href = urljoin(match_final, a["href"])
                if "/teams/" in href:
                    register_team_link(a.get_text(" ", strip=True), href)

        if len(links) == len(wanted):
            break

    return links


def _parse_team_matches(team, team_url, all_teams):
    """Lee partidos finalizados de un equipo desde su ficha de FMV."""
    html, _ = request_html(team_url, timeout=20)
    soup = BeautifulSoup(html, "html.parser")
    out = []

    for a in soup.find_all("a", href=True):
        href = urljoin(team_url, a["href"])
        if "/matches/" not in href:
            continue

        txt = normalize(a.get_text(" ", strip=True))
        if not txt:
            continue
        low = txt.lower()
        if "reubicacion" in low:
            phase = "reubic"
        elif "clasificacion" in low:
            phase = "class"
        else:
            continue

        # Si no hay resultado, FMV deja solo los nombres de los equipos.
        # En un partido jugado aparece inmediatamente un número 0..3 después
        # de cada nombre, antes de los tantos de los sets.
        def score_after(name):
            occ = list(re.finditer(
                r"(?<![A-Z0-9])" + re.escape(name) + r"(?![A-Z0-9])",
                txt, re.I
            ))
            if not occ:
                return None
            tail = txt[occ[-1].end():occ[-1].end() + 18]
            m = re.match(r"\s*([0-3])(?:\s|$)", tail)
            return int(m.group(1)) if m else None

        team_score = score_after(team)
        if team_score is None:
            continue

        # Encontrar el rival entre los nombres conocidos. Se excluye el propio
        # equipo y se toma la aparición más cercana al nombre del equipo.
        team_occ = list(re.finditer(
            r"(?<![A-Z0-9])" + re.escape(team) + r"(?![A-Z0-9])", txt, re.I
        ))
        if not team_occ:
            continue
        team_pos = team_occ[-1].start()

        candidates = []
        for other in all_teams:
            if normalize(other).upper() == normalize(team).upper():
                continue
            occ = list(re.finditer(
                r"(?<![A-Z0-9])" + re.escape(other) + r"(?![A-Z0-9])",
                txt, re.I
            ))
            if occ:
                candidates.append((other, occ[-1]))
        if not candidates:
            continue

        rival, rival_match = min(candidates, key=lambda x: abs(x[1].start() - team_pos))
        rival_score = score_after(rival)
        if rival_score is None or team_score == rival_score:
            continue

        match_id = re.search(r"/matches/(\d+)", href)
        match_id = match_id.group(1) if match_id else href
        out.append({
            "id": match_id,
            "phase": phase,
            "team": team,
            "rival": rival,
            "sf": team_score,
            "sa": rival_score,
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
    """Lee exclusivamente las tablas oficiales de Segunda etapa de FMV.

    No calcula puntos, no consulta partidos de equipos y no reconstruye
    Reubicación. FMV ya publica el PTS definitivo en cada tabla.
    """
    campeonato = None
    reubic = None

    views = discover_standings_views(
        source_url,
        tournament_id="539",
        known=KNOWN_STANDINGS,
    )

    # En algunas respuestas del frontend el selector de Segunda Etapa se
    # hidrata del lado del navegador y sus <option> no aparecen en el HTML
    # recibido por requests. En ese caso probamos únicamente IDs de GRUPO
    # cercanos al grupo oficial conocido (5974). Esto sigue leyendo tablas
    # oficiales de FMV: no calcula ni reconstruye ningún dato.
    #
    # La selección final se valida por la tabla encontrada y por su conjunto
    # de equipos; nunca se acepta una tabla arbitraria como Reubicación.
    for group_id in range(5970, 5986):
        views.append(
            f"https://metrovoley.com.ar/tournaments/539/standings?group={group_id}&stage=2067"
        )
    views = unique_keep_order(views)

    for url in views:
        try:
            html, final_url = request_html(url)
            tables = scrape_standings_url(final_url)
        except Exception:
            continue

        page_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
        url_low = final_url.lower()

        for item in tables:
            table = item["table"].copy()
            if len(table) != 8:
                continue
            if "PTS" not in table.columns:
                continue

            table = table.sort_values("Pos").reset_index(drop=True)
            teams = set(table["Equipo"].astype(str).str.upper())

            # La propia página/URL determina la rueda. Si no hay texto,
            # usamos el conjunto de equipos para no duplicar Campeonato.
            is_reubic = "reubic" in page_text or "reubic" in url_low
            is_champ = "campeonato" in page_text or "campeonato" in url_low

            if is_reubic:
                table["Pos"] = range(9, 17)
                reubic = {"table": table, "url": final_url}
            elif is_champ:
                table["Pos"] = range(1, 9)
                campeonato = {"table": table, "url": final_url}
            elif campeonato is None:
                # La URL conocida de group=5974/stage=2067 es la tabla de
                # Campeonato actual. Se acepta únicamente esa vista como
                # fallback de identificación, nunca como Reubicación.
                if "group=5974" in url_low and "stage=2067" in url_low:
                    table["Pos"] = range(1, 9)
                    campeonato = {"table": table, "url": final_url}
            else:
                champ_teams = set(campeonato["table"]["Equipo"].astype(str).str.upper())
                if teams != champ_teams and reubic is None:
                    table["Pos"] = range(9, 17)
                    reubic = {"table": table, "url": final_url}

        if campeonato is not None and reubic is not None:
            break

    result = {}
    if campeonato is not None:
        result["Rueda Campeonato"] = campeonato
    if reubic is not None:
        result["Rueda Reubicación"] = reubic
    return result

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
        errors.append("No se pudo localizar la tabla oficial de Rueda Reubicación en FMV.")

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
    border: 2px solid var(--verde);
    border-radius: 14px;
    padding: 14px;
    margin-bottom: 16px;
    background: linear-gradient(180deg, #ffffff 0%, #f4f4f4 100%);
    box-shadow: 0 4px 14px rgba(0,0,0,.10);
    min-height: 170px;
}
.match-title, .match-title * {
    color: var(--bordo) !important;
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
    border: 1px solid var(--bordo);
    border-radius: 8px;
    padding: 9px;
    background: #ffffff;
    font-weight: 650;
}
.vs {
    text-align: center;
    font-size: .78rem;
    font-weight: 800;
    color: var(--verde);
    padding: 5px 0;
}
.match-note {
    color: #333333 !important;
    margin-top: 10px;
    font-size: .75rem;
    color: #64748b;
}
.source-ok {
    padding: 10px 14px;
    border-radius: 10px;
    background: #eef7f1;
    border: 1px solid var(--verde);
}
div[data-testid="stAlert"] * { color: #111827 !important; }
.stDataFrame, .stDataFrame * { color: #111827 !important; }

/* Pestañas y títulos */
button[data-baseweb="tab"] {
    color: var(--verde) !important;
    font-weight: 700 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: var(--bordo) !important;
}

/* Encabezados */
h1, h2, h3 { color: var(--bordo) !important; }

/* Botón principal */
button[kind="primary"] {
    background: var(--bordo) !important;
    border-color: var(--bordo) !important;
}

.source-mock {
    padding: 10px 14px;
    border-radius: 10px;
    background: #fff1f3;
    border: 1px solid var(--bordo);
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("⚙️ Configuración")
st.sidebar.caption("Datos oficiales de FMV. La app lee la tabla de posiciones y usa el PTS publicado por FMV.")
url_cuarta = st.sidebar.text_input("URL Cuarta División", URL_CUARTA)
url_quinta = st.sidebar.text_input("URL Quinta División", URL_QUINTA)

refresh = st.sidebar.button(
    "🔄 Actualizar Datos en Vivo",
    type="primary",
    use_container_width=True,
)

if refresh or "data_loaded" not in st.session_state or "source_r" not in st.session_state:
    with st.spinner("Consultando datos oficiales de FMV..."):
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
        '<div class="source-ok">🟢 Cuarta: las posiciones se leen directamente de las tablas oficiales de FMV.</div>',
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
        st.caption("Numeración general 9° al 16°. PTS se toma directamente de la tabla oficial de FMV, incluyendo el arrastre que FMV ya publica.")
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
