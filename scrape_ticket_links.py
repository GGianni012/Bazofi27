#!/usr/bin/env python3
"""
Scraper BAFICI 27 - Extrae links de compra desde la API de bafici.org.
Matchea con schedule_data.js por fecha+hora.
Venue siempre de nuestro schedule. Buy URL siempre de la API.
Output: ticket_links.json con funciones matcheadas + links atados + diff.
"""

import json
import re
import sys
import ssl
import html
import unicodedata
import urllib.request
from datetime import datetime, date
from pathlib import Path
from difflib import SequenceMatcher

SCRIPT_DIR = Path(__file__).parent
SCHEDULE_FILE = SCRIPT_DIR / "schedule_data.js"
OUTPUT_FILE = SCRIPT_DIR / "ticket_links.json"
BAFICI_API = "https://bafici.org/wp-json/wp/v2/pelicula"


def load_schedule_data():
    if not SCHEDULE_FILE.exists():
        print(f"Warning: {SCHEDULE_FILE} no encontrado")
        return {}
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    json_str = content.replace("const scheduleData = ", "").rstrip().rstrip(";")
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"Error parsing schedule_data.js: {e}")
        return {}


def fetch_all_movies():
    ctx = ssl.create_default_context()
    all_movies = []
    page = 1
    per_page = 100
    while True:
        url = f"{BAFICI_API}?per_page={per_page}&page={page}&status=publish&_fields=id,title,acf"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                data = json.loads(response.read().decode())
            if not data:
                break
            all_movies.extend(data)
            print(f"  Pagina {page}: {len(data)} peliculas (total: {len(all_movies)})")
            if len(data) < per_page:
                break
            page += 1
        except Exception as e:
            print(f"  Error en pagina {page}: {e}")
            break
    return all_movies


def parse_api_date(dia_str):
    """Convierte '20260422' -> 'MIERCOLES 22'"""
    dia_str = dia_str.strip()
    match = re.match(r"(\d{4})(\d{2})(\d{2})$", dia_str)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        d = date(year, month, day)
        day_names = [
            "LUNES",
            "MARTES",
            "MIERCOLES",
            "JUEVES",
            "VIERNES",
            "SABADO",
            "DOMINGO",
        ]
        day_name = day_names[d.weekday()]
        return f"{day_name} {day}"
    return None


def normalize_title(title):
    title = html.unescape(title)
    title = title.strip()
    title = re.sub(r"\s+", " ", title)
    return title.lower()


def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def normalize_date(date_str):
    """Normaliza fechas removiendo acentos para comparacion."""
    date_str = date_str.strip().upper()
    date_str = unicodedata.normalize("NFD", date_str)
    date_str = "".join(c for c in date_str if not unicodedata.combining(c))
    return date_str


def find_movie_match(scraped_title, schedule_data):
    scraped_norm = normalize_title(scraped_title)
    for title in schedule_data:
        if normalize_title(title) == scraped_norm:
            return title, 1.0
    for title in schedule_data:
        our_norm = normalize_title(title)
        if scraped_norm.startswith(our_norm) or our_norm.startswith(scraped_norm):
            return title, 0.9
    best_match = None
    best_score = 0
    for title in schedule_data:
        score = similarity(scraped_norm, normalize_title(title))
        if score > best_score:
            best_score = score
            best_match = title
    if best_score > 0.75:
        return best_match, best_score
    return None, best_score


def scrape_bafici():
    schedule_data = load_schedule_data()
    print(f"Cargadas {len(schedule_data)} peliculas desde schedule_data.js")

    print(f"\n[1/3] Obteniendo data desde la API de bafici.org...")
    all_movies = fetch_all_movies()
    print(f"Total: {len(all_movies)} peliculas")

    print(f"\n[2/3] Matcheando funciones y atando links...")

    matched = []
    nuevas = []
    eliminadas = []
    sin_match_titulo = []

    matched_api_titles = set()
    matched_schedule_entries = set()

    for movie in all_movies:
        raw_title = movie.get("title", {}).get("rendered", "")
        api_title = html.unescape(raw_title)
        acf = movie.get("acf", {})
        funciones = acf.get("funciones", [])

        if not funciones:
            continue

        api_functions = []
        for f in funciones:
            dia = f.get("dia", "")
            hora = f.get("hora", "")
            link_compra = f.get("link_de_compra", "")
            estado = f.get("estado", "")
            gratuita = f.get("gratuita", False)

            parsed_date = parse_api_date(dia)
            if not parsed_date:
                continue

            api_functions.append(
                {
                    "date": parsed_date,
                    "time": hora,
                    "buy_url": link_compra if link_compra else None,
                    "estado": estado,
                    "gratuita": gratuita,
                }
            )

        if not api_functions:
            continue

        matched_title, score = find_movie_match(api_title, schedule_data)

        if not matched_title:
            sin_match_titulo.append(
                {
                    "api_title": api_title,
                    "raw_title": raw_title,
                    "best_score": round(score, 3),
                    "functions": api_functions,
                }
            )
            continue

        matched_api_titles.add(api_title)
        our_entries = schedule_data.get(matched_title, [])

        our_by_datetime = {}
        for i, entry in enumerate(our_entries):
            key = f"{normalize_date(entry['date'])}|{entry['time']}"
            our_by_datetime[key] = (i, entry)

        for api_func in api_functions:
            key = f"{normalize_date(api_func['date'])}|{api_func['time']}"
            if key in our_by_datetime:
                idx, our_entry = our_by_datetime[key]
                matched.append(
                    {
                        "movie": matched_title,
                        "date": api_func["date"],
                        "time": api_func["time"],
                        "venue": our_entry["venue"],
                        "section": our_entry.get("section", ""),
                        "buy_url": api_func["buy_url"],
                        "estado": api_func["estado"],
                        "gratuita": api_func["gratuita"],
                    }
                )
                matched_schedule_entries.add(f"{matched_title}|{key}")
            else:
                nuevas.append(
                    {
                        "movie": matched_title,
                        "date": api_func["date"],
                        "time": api_func["time"],
                        "venue": "POR VERIFICAR",
                        "buy_url": api_func["buy_url"],
                        "estado": api_func["estado"],
                        "gratuita": api_func["gratuita"],
                    }
                )

    for title, entries in schedule_data.items():
        for entry in entries:
            key = f"{normalize_date(entry['date'])}|{entry['time']}"
            if f"{title}|{key}" not in matched_schedule_entries:
                eliminadas.append(
                    {
                        "movie": title,
                        "date": entry["date"],
                        "time": entry["time"],
                        "venue": entry["venue"],
                        "section": entry.get("section", ""),
                    }
                )

    print(f"\n[3/3] Resultados...")

    output = {
        "scraped_at": datetime.now().isoformat(),
        "source": "WP REST API - bafici.org",
        "stats": {
            "total_movies_in_api": len(all_movies),
            "total_functions_in_api": sum(
                len(m.get("acf", {}).get("funciones", [])) for m in all_movies
            ),
            "total_functions_in_schedule": sum(len(v) for v in schedule_data.values()),
            "matched_with_link": len(matched),
            "nuevas": len(nuevas),
            "eliminadas": len(eliminadas),
            "sin_match_titulo": len(sin_match_titulo),
        },
        "matched_functions": matched,
        "diff": {
            "nuevas": nuevas,
            "eliminadas": eliminadas,
            "sin_match_titulo": sin_match_titulo,
        },
    }

    return output


def print_summary(result):
    stats = result["stats"]
    diff = result["diff"]

    print(f"\n{'=' * 60}")
    print(f"BAFICI 27 - Scraper + Diff")
    print(f"{'=' * 60}")
    print(f"  Peliculas en API:         {stats['total_movies_in_api']}")
    print(f"  Funciones en API:         {stats['total_functions_in_api']}")
    print(f"  Funciones en schedule.js: {stats['total_functions_in_schedule']}")
    print(f"  ---")
    print(f"  MATCH CON LINK:  {stats['matched_with_link']}")
    print(f"  NUEVAS:          {stats['nuevas']} (en API pero no en schedule)")
    print(f"  ELIMINADAS:      {stats['eliminadas']} (en schedule pero no en API)")
    print(f"  SIN MATCH:       {stats['sin_match_titulo']} (titulos sin match)")

    if diff["nuevas"]:
        print(f"\n--- NUEVAS FUNCIONES ({len(diff['nuevas'])}) ---")
        for item in diff["nuevas"][:30]:
            buy = f" -> {item['buy_url']}" if item.get("buy_url") else ""
            print(
                f"  {item['movie']}: {item['date']} {item['time']} @ {item['venue']}{buy}"
            )
        if len(diff["nuevas"]) > 30:
            print(f"  ... y {len(diff['nuevas']) - 30} mas")

    if diff["eliminadas"]:
        print(f"\n--- FUNCIONES ELIMINADAS ({len(diff['eliminadas'])}) ---")
        for item in diff["eliminadas"][:30]:
            print(f"  {item['movie']}: {item['date']} {item['time']} @ {item['venue']}")
        if len(diff["eliminadas"]) > 30:
            print(f"  ... y {len(diff['eliminadas']) - 30} mas")

    if diff["sin_match_titulo"]:
        print(f"\n--- TITULOS SIN MATCH ({len(diff['sin_match_titulo'])}) ---")
        for item in diff["sin_match_titulo"]:
            print(f"  '{item['api_title']}' (score: {item['best_score']})")


def main():
    print("=" * 60)
    print("BAFICI 27 - Scraper + Diff (via WP REST API)")
    print("=" * 60)
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    result = scrape_bafici()

    if result is None:
        print("\nError: No se pudo scrapear la pagina")
        sys.exit(1)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print_summary(result)

    print(f"\nOutput completo: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
