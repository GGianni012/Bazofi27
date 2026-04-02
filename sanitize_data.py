#!/usr/bin/env python3
"""
BAFICI 27 - Data Sanitization Script
Merges descriptions, fixes white images, enriches metadata.
Does NOT delete any backup files.
"""

import json
import os
import re
import shutil
from difflib import SequenceMatcher

BASE = "/Users/SoniaSantoro/Downloads/bafici 27"
PUBLIC = os.path.join(BASE, "public")
DEPLOY = os.path.join(BASE, "deploy")
IMG_DIR_PUBLIC = os.path.join(PUBLIC, "movie_images")
IMG_DIR_DEPLOY = os.path.join(DEPLOY, "movie_images")
DESC_DIR = os.path.join(PUBLIC, "descriptions")  # Also in BASE/descriptions


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_js(path, var_name, data):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"const {var_name} = ")
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write(";\n")


def normalize_title(title):
    """Normalize title for matching: lowercase, strip accents, strip punctuation."""
    t = title.lower().strip()
    # Remove common prefixes/suffixes
    t = re.sub(r"[¿¡!?,.:;\"'()\\[\\]{}]", "", t)
    t = t.strip()
    return t


def is_white_image(path):
    """Check if an image is mostly white/blank."""
    try:
        from PIL import Image
        img = Image.open(path)
        w, h = img.size
        sample_points = [
            (w // 2, h // 2), (w // 4, h // 4),
            (3 * w // 4, 3 * h // 4), (10, 10),
            (w - 10, h - 10), (w // 2, 10)
        ]
        white_count = 0
        for x, y in sample_points:
            try:
                p = img.getpixel((min(x, w - 1), min(y, h - 1)))
                if isinstance(p, tuple) and len(p) >= 3:
                    if p[0] > 235 and p[1] > 235 and p[2] > 235:
                        white_count += 1
                elif isinstance(p, int) and p > 235:
                    white_count += 1
            except Exception:
                pass
        return white_count >= 4  # At least 4 of 6 sample points are white
    except Exception:
        return False


def find_best_alternative_image(page_num, search_dir):
    """Find the best non-white alternative image for a given page."""
    pattern = f"movie_p{page_num:03d}"
    candidates = []
    for f in os.listdir(search_dir):
        if f.startswith(pattern) and f.endswith(".jpg") and not f.startswith("movie_p" + f"{page_num:03d}" + "."):
            # This is a movie_pNNN_N.jpg file
            full_path = os.path.join(search_dir, f)
            if not is_white_image(full_path):
                candidates.append((f, os.path.getsize(full_path)))

    if candidates:
        # Return the largest non-white image
        best = max(candidates, key=lambda x: x[1])
        return best[0]
    return None


def read_description_file(page_num):
    """Try reading description from description_pNNN.txt files."""
    for desc_dir in [os.path.join(BASE, "descriptions"), DESC_DIR]:
        path = os.path.join(desc_dir, f"description_p{page_num}.txt")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if len(content) > 30:
                    return content
            except Exception:
                pass
    return None


def extract_description_from_text(raw_text, title):
    """Extract just the description part from a raw page text extraction."""
    if not raw_text:
        return ""

    lines = raw_text.split("\n")
    # Remove the title and header lines, keep the description body
    # Description usually starts after technical info (country, year, duration)
    # and is the longest paragraph

    # Find where the description likely starts
    desc_lines = []
    found_tech = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip short header lines and title
        if len(line) < 30 and not found_tech:
            # Check if this is tech info
            if re.search(r"\d{4},\s*\d+['\u2032\u2019\u2018]", line):
                found_tech = True
            continue
        if found_tech or len(line) > 50:
            found_tech = True
            desc_lines.append(line)

    text = " ".join(desc_lines).strip()

    # Remove the title from the beginning if present
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].strip()

    # Clean up
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > 30:
        return text[:1500]  # Cap at reasonable length
    return ""


def main():
    print("=" * 60)
    print("BAFICI 27 - Sanitización de datos")
    print("=" * 60)

    # ========== LOAD ALL DATA SOURCES ==========
    print("\n📂 Cargando datos...")

    current = load_json(os.path.join(BASE, "movies_data.json"))
    print(f"  movies_data.json: {len(current)} películas")

    web = load_json(os.path.join(BASE, "movies_for_web.json"))
    print(f"  movies_for_web.json: {len(web)} películas")

    improved = load_json(os.path.join(BASE, "movies_data_improved.json"))
    print(f"  movies_data_improved.json: {len(improved)} películas")

    # ========== BUILD LOOKUP INDEXES ==========
    print("\n🔍 Construyendo índices...")

    # Web data by normalized title
    web_by_title = {}
    for m in web:
        key = normalize_title(m["title"])
        web_by_title[key] = m

    # Improved data by normalized title
    improved_by_title = {}
    for m in improved:
        key = normalize_title(m["title"])
        improved_by_title[key] = m

    # Web data by page number
    web_by_page = {}
    for m in web:
        if m.get("page"):
            web_by_page[m["page"]] = m

    # Improved data by page number
    improved_by_page = {}
    for m in improved:
        if m.get("page"):
            improved_by_page[m["page"]] = m

    # ========== ENRICH MOVIES ==========
    print("\n🔧 Enriqueciendo datos...")

    stats = {
        "desc_added": 0,
        "desc_from_web": 0,
        "desc_from_improved": 0,
        "desc_from_file": 0,
        "desc_already": 0,
        "desc_missing": 0,
        "title_en_added": 0,
        "year_added": 0,
        "page_added": 0,
        "white_fixed": 0,
        "white_no_fix": 0,
    }

    for m in current:
        title_key = normalize_title(m["title"])

        # Extract page number from image path
        img = m.get("image", "")
        page_from_img = None
        match = re.search(r"movie_(\d{3})\.jpg", img)
        if match:
            page_from_img = int(match.group(1))

        # --- FIND MATCHING RECORD ---
        source = None
        source_name = None

        # 1. Try exact title match in web data
        if title_key in web_by_title:
            source = web_by_title[title_key]
            source_name = "web"
        # 2. Try exact title match in improved data
        elif title_key in improved_by_title:
            source = improved_by_title[title_key]
            source_name = "improved"
        else:
            # 3. Try fuzzy title match
            best_score = 0
            best_match = None
            best_src = None
            for wt, wm in web_by_title.items():
                score = SequenceMatcher(None, title_key, wt).ratio()
                if score > best_score:
                    best_score = score
                    best_match = wm
                    best_src = "web"
            for it, im in improved_by_title.items():
                score = SequenceMatcher(None, title_key, it).ratio()
                if score > best_score:
                    best_score = score
                    best_match = im
                    best_src = "improved"

            if best_score >= 0.85:
                source = best_match
                source_name = best_src

        # 4. Try page-based match if we have a page number
        if source is None and page_from_img:
            if page_from_img in web_by_page:
                candidate = web_by_page[page_from_img]
                # Only use if title is somewhat similar (avoid wrong page assignments)
                score = SequenceMatcher(
                    None, title_key, normalize_title(candidate["title"])
                ).ratio()
                if score >= 0.5:
                    source = candidate
                    source_name = "web-page"

        # --- MERGE DESCRIPTION ---
        has_desc = bool(m.get("description", "").strip())

        if has_desc:
            stats["desc_already"] += 1
        elif source and source.get("description", "").strip():
            m["description"] = source["description"].strip()
            stats["desc_added"] += 1
            stats[f"desc_from_{source_name.split('-')[0]}"] += 1
        else:
            # Try description files
            if page_from_img:
                raw = read_description_file(page_from_img)
                if raw:
                    # The description files contain the full page text
                    # We need to extract just the description part
                    desc = extract_description_from_text(raw, m["title"])
                    if desc:
                        m["description"] = desc
                        stats["desc_added"] += 1
                        stats["desc_from_file"] += 1
                    else:
                        stats["desc_missing"] += 1
                else:
                    stats["desc_missing"] += 1
            else:
                stats["desc_missing"] += 1

        # --- MERGE OTHER METADATA ---
        if source:
            # Add page number if missing
            if not m.get("page") or not str(m.get("page")).strip():
                if source.get("page"):
                    m["page"] = source["page"]
                    stats["page_added"] += 1
                elif page_from_img:
                    m["page"] = page_from_img
                    stats["page_added"] += 1

            # Add English title if missing
            if not m.get("title_en") and source.get("title_en"):
                m["title_en"] = source["title_en"]
                stats["title_en_added"] += 1

            # Add year if missing
            if not m.get("year") and source.get("year"):
                m["year"] = source["year"]
                stats["year_added"] += 1
        else:
            # Still try to set page from image
            if (not m.get("page") or not str(m.get("page")).strip()) and page_from_img:
                m["page"] = page_from_img
                stats["page_added"] += 1

    # ========== FIX WHITE IMAGES ==========
    print("\n🖼️  Reparando imágenes blancas...")

    for m in current:
        img = m.get("image", "")
        if not img:
            continue

        # Check in both public and deploy directories
        for img_base in [PUBLIC, DEPLOY]:
            full_path = os.path.join(img_base, img)
            if os.path.exists(full_path) and is_white_image(full_path):
                # Extract page number
                match = re.search(r"movie_(\d{3})\.jpg", img)
                if not match:
                    continue
                page_num = int(match.group(1))

                # Find alternative in public root (movie_pNNN_N.jpg files)
                alt = find_best_alternative_image(page_num, PUBLIC)
                if alt:
                    src = os.path.join(PUBLIC, alt)
                    # Copy to movie_images with the original name
                    dst = full_path
                    shutil.copy2(src, dst)
                    print(f"  ✅ Reemplazó {img} con {alt}")
                    stats["white_fixed"] += 1
                else:
                    print(f"  ❌ Sin alternativa para {img} (page {page_num})")
                    stats["white_no_fix"] += 1
                break  # Only fix once per movie

    # Also fix in the other directory
    for m in current:
        img = m.get("image", "")
        if not img:
            continue
        match = re.search(r"movie_(\d{3})\.jpg", img)
        if not match:
            continue
        page_num = int(match.group(1))

        # Ensure both public and deploy have the same (fixed) image
        pub_path = os.path.join(PUBLIC, img)
        dep_path = os.path.join(DEPLOY, img)
        if os.path.exists(pub_path) and os.path.exists(dep_path):
            # If public was fixed but deploy wasn't, sync
            if not is_white_image(pub_path) and is_white_image(dep_path):
                shutil.copy2(pub_path, dep_path)

    # ========== SAVE RESULTS ==========
    print("\n💾 Guardando datos sanitizados...")

    # Save JSON
    save_json(os.path.join(BASE, "movies_data.json"), current)
    save_json(os.path.join(PUBLIC, "movies_data.json"), current)
    save_json(os.path.join(DEPLOY, "movies_data.json"), current)

    # Save JS
    save_js(os.path.join(BASE, "movies_data.js"), "moviesData", current)
    save_js(os.path.join(PUBLIC, "movies_data.js"), "moviesData", current)
    save_js(os.path.join(DEPLOY, "movies_data.js"), "moviesData", current)

    # ========== REPORT ==========
    print("\n" + "=" * 60)
    print("📊 RESULTADOS")
    print("=" * 60)

    total = len(current)
    with_desc = sum(1 for m in current if m.get("description", "").strip())
    with_img = sum(1 for m in current if m.get("image", "").strip())
    with_page = sum(1 for m in current if m.get("page") and str(m.get("page")).strip())
    with_year = sum(1 for m in current if m.get("year", "").strip())
    with_title_en = sum(1 for m in current if m.get("title_en", "").strip())
    with_director = sum(1 for m in current if m.get("director", "").strip())

    print(f"\n  Total películas: {total}")
    print(f"  Con descripción: {with_desc}/{total} ({100*with_desc//total}%)")
    print(f"    - Ya tenían: {stats['desc_already']}")
    print(f"    - Agregadas: {stats['desc_added']}")
    print(f"      · desde web: {stats['desc_from_web']}")
    print(f"      · desde improved: {stats['desc_from_improved']}")
    print(f"      · desde archivos: {stats['desc_from_file']}")
    print(f"    - Sin encontrar: {stats['desc_missing']}")
    print(f"  Con imagen: {with_img}/{total}")
    print(f"  Con página: {with_page}/{total}")
    print(f"  Con año: {with_year}/{total}")
    print(f"  Con título EN: {with_title_en}/{total}")
    print(f"  Con director: {with_director}/{total}")
    print(f"\n  Imágenes blancas corregidas: {stats['white_fixed']}")
    print(f"  Imágenes blancas sin fix: {stats['white_no_fix']}")
    print(f"  Páginas agregadas: {stats['page_added']}")
    print(f"  Títulos EN agregados: {stats['title_en_added']}")
    print(f"  Años agregados: {stats['year_added']}")

    # Show movies still without description
    missing = [m["title"] for m in current if not m.get("description", "").strip()]
    if missing:
        print(f"\n  ⚠️  Películas sin descripción ({len(missing)}):")
        for t in sorted(missing):
            print(f"    · {t}")


if __name__ == "__main__":
    main()
