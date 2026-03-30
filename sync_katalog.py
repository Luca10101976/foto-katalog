#!/usr/bin/env python3
"""
sync_katalog.py — Automatická synchronizace foto katalogu
Najde nové fotky na Google Drive, popíše je Claudem, aktualizuje katalog a pushne na GitHub.
Použití: python3 sync_katalog.py
"""

import os, json, base64, time, subprocess, sys
from pathlib import Path

# ── Konfigurace ──────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CREDENTIALS_FILE  = Path(__file__).parent / "credentials.json"
TOKEN_FILE        = Path(__file__).parent / "token.json"
KATALOG_FILE      = Path(__file__).parent / "katalog_data.json"
LOG_FILE          = Path(__file__).parent / "sync_log.txt"

OBSAH_OPTIONS = [
    "Moře / Pobřeží","Vodopád","Řeka / Jezero","Hory / Krajina",
    "Příroda / Rostliny","Mořský život","Zvíře","Původní obyvatelé",
    "Lidé — portréty","Lidé","Jídlo / Trh","Památky / Ruiny",
    "Město / Architektura","Loď / Plavba","Mapa / Dokument","Ostatní"
]

IMAGE_MIMES = {
    "image/jpeg","image/jpg","image/png","image/heic",
    "image/heif","image/webp","image/tiff"
}
VIDEO_MIMES = {
    "video/mp4","video/quicktime","video/x-msvideo","video/mpeg"
}

# ── Závislosti ────────────────────────────────────────────────────────────────
def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

try:
    import anthropic
except ImportError:
    print("📦 Instaluji anthropic..."); install("anthropic"); import anthropic

try:
    from PIL import Image as PILImage
except ImportError:
    print("📦 Instaluji Pillow..."); install("Pillow"); from PIL import Image as PILImage

# HEIC podpora
HEIC_SUPPORT = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_SUPPORT = True
except ImportError:
    try:
        install("pillow-heif")
        import pillow_heif
        pillow_heif.register_heif_opener()
        HEIC_SUPPORT = True
    except Exception:
        pass  # Budeme fallback na thumbnail

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except ImportError:
    print("📦 Instaluji google-api..."); install("google-api-python-client google-auth-oauthlib");
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

import io, mimetypes

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
ROOT_FOLDER_ID = "1xZpgkTbOpzoQ53ilA2Tg-n9CCdSQWlDJ"  # Martin Guía — hlavní složka s fotkami
EXTRA_FOLDERS = ["1JtGPADJKIGaNxk0BM-C-KBmILh0Sds6t"]   # Sdílené složky (Senidub...)

# ── Google Drive auth ─────────────────────────────────────────────────────────
def get_drive_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)

# ── Načti katalog ─────────────────────────────────────────────────────────────
def load_katalog():
    if KATALOG_FILE.exists():
        return json.loads(KATALOG_FILE.read_text(encoding="utf-8"))
    return []

# ── Najdi nové fotky na Drive ─────────────────────────────────────────────────
# Názvy souborů/složek které přeskočit (systémové soubory)
SKIP_PATTERNS = ["thumb_", ".thumb", "thumbdata", ".thumbnails", "Thumbs.db",
                 ".DS_Store", "desktop.ini", ".tmp", "~$"]

def is_real_photo(filename):
    """Vrátí False pokud jde o systémový soubor."""
    name_lower = filename.lower()
    for pat in SKIP_PATTERNS:
        if pat.lower() in name_lower:
            return False
    return True

def get_all_subfolder_ids(service, folder_id):
    """Rekurzivně najde všechna ID podsložek."""
    ids = [folder_id]
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="nextPageToken, files(id)",
            pageSize=1000,
            pageToken=page_token
        ).execute()
        for f in resp.get("files", []):
            ids += get_all_subfolder_ids(service, f["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids

def get_new_files(service, existing_ids):
    print("🔍 Zjišťuji strukturu složek...")
    folder_ids = get_all_subfolder_ids(service, ROOT_FOLDER_ID)
    for extra in EXTRA_FOLDERS:
        folder_ids += get_all_subfolder_ids(service, extra)
    print(f"   📁 Nalezeno {len(folder_ids)} složek ve foto archivu")

    new_files = []
    mime_query = " or ".join([f"mimeType='{m}'" for m in IMAGE_MIMES | VIDEO_MIMES])
    folder_cache = {}
    scanned = 0

    for folder_id in folder_ids:
        # Zjisti název složky
        if folder_id not in folder_cache:
            try:
                meta = service.files().get(fileId=folder_id, fields="name").execute()
                folder_cache[folder_id] = meta.get("name", "")
            except:
                folder_cache[folder_id] = ""
        folder_name = folder_cache[folder_id]

        page_token = None
        while True:
            query = f"'{folder_id}' in parents and trashed=false and ({mime_query})"
            resp = service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, size, imageMediaMetadata)",
                pageSize=1000,
                pageToken=page_token
            ).execute()

            for f in resp.get("files", []):
                scanned += 1
                if f["id"] in existing_ids:
                    continue
                if not is_real_photo(f["name"]):
                    continue
                if int(f.get("size", 0)) < 50000:
                    continue
                # GPS z Drive metadata
                drive_gps = ""
                loc = (f.get("imageMediaMetadata") or {}).get("location", {})
                if loc and loc.get("latitude") and loc.get("longitude"):
                    drive_gps = f"{loc['latitude']:.6f},{loc['longitude']:.6f}"
                new_files.append({
                    "id": f["id"],
                    "nazev": f["name"],
                    "kat": folder_name,
                    "mime": f["mimeType"],
                    "drive_gps": drive_gps
                })

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    print(f"   ✅ Prohledáno {scanned} fotek, nalezeno {len(new_files)} nových")
    return new_files

# ── Stáhni náhled fotky ───────────────────────────────────────────────────────
def convert_dng_to_jpeg(raw_bytes):
    """Převede DNG/RAW na JPEG."""
    try:
        import rawpy, imageio
        with rawpy.imread(io.BytesIO(raw_bytes)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, half_size=True, no_auto_bright=False)
        buf = io.BytesIO()
        imageio.imwrite(buf, rgb, format='jpeg')
        return buf.getvalue()
    except Exception as e:
        return None

def resize_if_needed(image_bytes, max_bytes=3_500_000):
    """Zmenší obrázek pokud je větší než max_bytes."""
    if len(image_bytes) <= max_bytes:
        return image_bytes
    print(f"   🔄 Zmenšuji obrázek ({len(image_bytes)//1024}KB → max {max_bytes//1024}KB)...", end=" ", flush=True)
    try:
        img = PILImage.open(io.BytesIO(image_bytes))
        # Převeď na RGB (pro případ RGBA/palette)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        scale = 1.0
        while scale >= 0.2:
            scale *= 0.8
            buf = io.BytesIO()
            w, h = max(1, int(img.width * scale)), max(1, int(img.height * scale))
            resized = img.resize((w, h), PILImage.LANCZOS)
            resized.save(buf, format='JPEG', quality=75, optimize=True)
            result = buf.getvalue()
            if len(result) <= max_bytes:
                print(f"ok ({len(result)//1024}KB)", flush=True)
                return result
        # Krajní případ - nejmenší možná velikost
        buf = io.BytesIO()
        img.resize((800, int(800 * img.height / img.width)), PILImage.LANCZOS).save(buf, format='JPEG', quality=60)
        return buf.getvalue()
    except Exception as e:
        print(f"   ⚠️  resize selhal: {e}")
        return image_bytes

def heic_to_jpeg(raw_data):
    """Převede HEIC na JPEG pomocí pillow-heif."""
    if not HEIC_SUPPORT:
        return None
    try:
        img = PILImage.open(io.BytesIO(raw_data))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        return buf.getvalue()
    except Exception:
        return None

def get_image_bytes(service, file_id, mime_type, filename=""):
    is_raw = filename.lower().endswith(('.dng', '.cr2', '.nef', '.arw', '.raf'))
    is_heic = filename.lower().endswith(('.heic', '.heif'))
    is_video = mime_type in VIDEO_MIMES
    import urllib.request

    # Pro video použij VŽDY jen thumbnail — nikdy nestahuj celý soubor
    if is_video:
        try:
            url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w1600"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            if len(data) > 1000 and (data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n'):
                return data, "image/jpeg"
        except:
            pass
        return None, None

    # Pro JPEG/PNG/WebP zkus nejdřív thumbnail (rychlejší, menší, funguje i pro sdílené složky)
    if not is_raw and not is_heic:
        try:
            url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w1600"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            if len(data) > 1000 and (data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n'):
                return data, "image/jpeg"
        except:
            pass

    # Stáhni originál přes API
    try:
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        raw_data = buf.getvalue()

        if is_raw:
            jpeg = convert_dng_to_jpeg(raw_data)
            if jpeg:
                return jpeg, "image/jpeg"
            # DNG konverze selhala — fallback na thumbnail níže
        elif is_heic:
            # HEIC: zkus konverzi přes pillow-heif
            jpeg = heic_to_jpeg(raw_data)
            if jpeg:
                return jpeg, "image/jpeg"
            # pillow-heif selhalo — fallback na thumbnail níže
        else:
            return raw_data, mime_type
    except:
        pass

    # Fallback pro sdílené složky — thumbnail (funguje i pro DNG/RAW)
    try:
        url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w1600"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if len(data) > 1000 and (data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n'):
            return data, "image/jpeg"
    except:
        pass

    return None, None

# ── Detekuj skutečný typ obrázku ──────────────────────────────────────────────
def detect_mime(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if data[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    if data[:4] in (b'RIFF', b'WEBP'):
        return "image/webp"
    return "image/jpeg"

# ── Popis fotky Claudem ───────────────────────────────────────────────────────
def describe_with_claude(client, image_data, mime_type, folder_name):
    obsah_list = "\n".join([f"- {o}" for o in OBSAH_OPTIONS])
    prompt = f"""Popiš tuto fotografii v češtině. DŮLEŽITÉ: Piš POUZE latinkou, nikdy nepoužívej azbuku ani jiné nelatinskéznaková sady. Název složky (jen nápověda): "{folder_name}".

Vrať POUZE JSON v tomto formátu (bez markdown, bez komentářů):
{{
  "popis": "Krátký výstižný popis co je na fotce (1-2 věty)",
  "obsah": "jedna z kategorií níže",
  "druh": "pokud je na fotce zvíře nebo rostlina, napiš český název druhu, jinak null",
  "kat": "správný název lokace nebo tématu (např. 'Panama, Guna Yala', 'Costa Rica, La Fortuna', 'Senidub — příroda', 'Panama, kanál'). Použij název složky jako nápovědu, ale pojmenuj sám podle toho co vidíš."
}}

Kategorie obsahu (vyber jednu):
{obsah_list}

Pravidla:
- popis: konkrétní, popisuj co vidíš (místa, akce, objekty, barvy)
- obsah: vyber nejlépe odpovídající kategorii
- druh: pouze pokud jsi si jistý/á (≥70%), jinak null
- kat: výstižný název místa nebo tématu, ne systémový název složky jako "čeká zpracuj" nebo "Hlavní složka" — ten nahraď smysluplným názvem"""

    real_mime = detect_mime(image_data)
    b64 = base64.standard_b64encode(image_data).decode("utf-8")

    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": real_mime, "data": b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    )

    text = msg.content[0].text.strip()
    # Vyčisti markdown
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    return json.loads(text)

# ── GPS extrakce z EXIF ───────────────────────────────────────────────────────
def extract_gps(image_data):
    try:
        import exifread
        tags = exifread.process_file(io.BytesIO(image_data), details=False)

        def to_deg(val):
            d, m, s = [float(x.num)/float(x.den) for x in val.values]
            return d + m/60 + s/3600

        lat = tags.get("GPS GPSLatitude")
        lat_ref = tags.get("GPS GPSLatitudeRef")
        lon = tags.get("GPS GPSLongitude")
        lon_ref = tags.get("GPS GPSLongitudeRef")

        if lat and lon:
            lat_deg = to_deg(lat)
            lon_deg = to_deg(lon)
            if str(lat_ref) == "S": lat_deg = -lat_deg
            if str(lon_ref) == "W": lon_deg = -lon_deg
            return f"{lat_deg:.6f},{lon_deg:.6f}"
    except:
        pass
    return None

# ── Přiřazení misto z názvu složky + GPS ──────────────────────────────────────
import re as _re

def folder_to_misto(folder_name, gps=""):
    """Z názvu složky (a GPS zálohy) odvodí čisté jméno místa pro dropdown."""
    f = folder_name.strip()
    fl = f.lower()

    # Detekce země
    is_nik = any(k in fl for k in ("nikaragua", "nicaragua"))
    is_pan = any(k in fl for k in ("panama", "panamá"))
    is_cr  = any(k in fl for k in ("kostarika", "costa rica"))

    # Záloha: detekce země z GPS souřadnic
    if not (is_nik or is_pan or is_cr) and gps:
        try:
            lat, lon = map(float, gps.split(","))
            if lat > 10.5:
                is_nik = True
            elif lon < -84 or (lat > 8 and lon < -82.5):
                is_cr = True
            else:
                is_pan = True
        except:
            pass

    # Parsování složek ve formátu "Costa Rica, Město, ..., datum"
    # → extrahuje konkrétní název místa
    def extract_city(name, country_keyword):
        # Odstraň datum (XX.XX.XXXX) z konce
        name = _re.sub(r',?\s*\d{2}\.\d{2}\.\d{4}$', '', name).strip()
        # Rozdělení po čárce, odstraň část se zemí
        parts = [p.strip() for p in name.split(',')]
        city_parts = [p for p in parts if country_keyword.lower() not in p.lower() and p]
        if city_parts:
            return ' / '.join(city_parts)
        return None

    if is_cr:
        city = extract_city(f, 'costa rica') or extract_city(f, 'kostarika')
        if city and city.lower() not in ('kostarika', 'costa rica'):
            return city + ', Kostarika'
        return 'Kostarika'

    if is_nik:
        city = extract_city(f, 'nikaragua') or extract_city(f, 'nicaragua')
        if city and city.lower() not in ('nikaragua', 'nicaragua'):
            return city + ', Nikaragua'
        return 'Nikaragua'

    if is_pan:
        city = extract_city(f, 'panama') or extract_city(f, 'panamá')
        if city and city.lower() not in ('panama', 'panamá'):
            return city + ', Panama'
        return 'Panama'

    # Neznámá země — vrátíme jak je
    return f

# ── Hlavní funkce ─────────────────────────────────────────────────────────────
def main():
    print("🚀 Spouštím sync_katalog.py")
    print("=" * 50)

    # Načti existující katalog
    katalog = load_katalog()
    existing_ids = {item["id"] for item in katalog}
    print(f"📚 Katalog: {len(katalog)} fotek")

    # Připoj se k Drive
    print("🔐 Přihlašuji se k Google Drive...")
    service = get_drive_service()

    # Najdi nové fotky
    new_files = get_new_files(service, existing_ids)

    if not new_files:
        print("✅ Žádné nové fotky nenalezeny!")
        return

    print(f"🆕 Nalezeno {len(new_files)} nových fotek")

    # Inicializuj Claude
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Zpracuj nové fotky
    added = 0
    errors = 0

    with open(LOG_FILE, "a", encoding="utf-8") as log:
        for i, f in enumerate(new_files, 1):
            short_name = f["nazev"][:50]
            print(f"[{i}/{len(new_files)}] {short_name}...", end=" ", flush=True)

            try:
                # Stáhni obrázek
                img_data, mime = get_image_bytes(service, f["id"], f["mime"], f["nazev"])
                if not img_data:
                    print("⚠️  nelze stáhnout")
                    errors += 1
                    continue

                # GPS: nejdřív Drive metadata, pak EXIF
                gps = f.get("drive_gps") or extract_gps(img_data)

                # Zmenši pokud je obrázek příliš velký pro Claude API
                img_data = resize_if_needed(img_data)

                # Popis od Claudea
                result = describe_with_claude(client, img_data, mime, f["kat"])

                # Vytvoř záznam — kat preferuje Claudův název, fallback na název složky
                kat = result.get("kat") or f["kat"]
                entry = {
                    "id": f["id"],
                    "popis": result.get("popis", ""),
                    "kat": kat,
                    "misto": folder_to_misto(f["kat"], gps or ""),
                    "obsah": result.get("obsah", "Ostatní"),
                    "gps": gps or "",
                    "druh": result.get("druh", None),
                    "nazev": f["nazev"],
                    "zdroj": "vlastní",
                    "heic": f["nazev"].lower().endswith(".heic"),
                    "video": f["mime"] in VIDEO_MIMES
                }

                katalog.append(entry)
                added += 1
                print(f"✅ {result.get('obsah', '?')}")
                log.write(f"[{i}] {f['nazev']} → {result.get('obsah')}\n")

                # Průběžně ukládej každých 10 fotek
                if added % 10 == 0:
                    KATALOG_FILE.write_text(json.dumps(katalog, ensure_ascii=False, separators=(',', ':')), encoding="utf-8")
                    print(f"    💾 Průběžně uloženo ({added} nových)")

                time.sleep(0.3)  # Rate limiting

            except Exception as e:
                print(f"⚠️  chyba: {e}")
                errors += 1
                log.write(f"[{i}] CHYBA {f['nazev']}: {e}\n")

    # Ulož finální katalog
    KATALOG_FILE.write_text(json.dumps(katalog, ensure_ascii=False, separators=(',', ':')), encoding="utf-8")
    print(f"\n✅ Hotovo! Přidáno {added} nových fotek, {errors} chyb")

    # Push na GitHub
    if added > 0:
        print("\n🚀 Pushuji na GitHub...")
        try:
            os.chdir(Path(__file__).parent)
            subprocess.run(["git", "add", "katalog_data.json"], check=True)
            subprocess.run(["git", "commit", "-m", f"Přidáno {added} nových fotek [auto-sync]"], check=True)
            subprocess.run(["git", "push"], check=True)
            print("✅ GitHub aktualizován! Galerie se zaktualizuje za ~1 minutu.")
        except Exception as e:
            print(f"⚠️  GitHub push selhal: {e}")
            print("   Můžeš pushovat manuálně: git push")

if __name__ == "__main__":
    main()
