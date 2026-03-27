#!/usr/bin/env python3
"""Zpracuje pouze nové fotky ze složky Senidub."""

import os, json, base64, sys, subprocess
from pathlib import Path

def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

try:
    import anthropic
except ImportError:
    install("anthropic"); import anthropic

try:
    from PIL import Image as PILImage
except ImportError:
    install("Pillow"); from PIL import Image as PILImage

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io, urllib.request

KATALOG_FILE = Path(__file__).parent / "katalog_data.json"
TOKEN_FILE    = Path(__file__).parent / "token.json"
SENIDUB_ID   = "1JtGPADJKIGaNxk0BM-C-KBmILh0Sds6t"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

def get_service():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    return build("drive", "v3", credentials=creds)

def get_thumbnail(file_id):
    url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w1600"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read()
    if len(data) > 5000 and data[:3] == b'\xff\xd8\xff':
        return data
    return None

def resize(image_bytes, max_bytes=3_500_000):
    if len(image_bytes) <= max_bytes:
        return image_bytes
    img = PILImage.open(io.BytesIO(image_bytes))
    if img.mode not in ('RGB','L'):
        img = img.convert('RGB')
    scale = 0.8
    while scale >= 0.2:
        buf = io.BytesIO()
        w, h = max(1,int(img.width*scale)), max(1,int(img.height*scale))
        img.resize((w,h), PILImage.LANCZOS).save(buf, format='JPEG', quality=75)
        if len(buf.getvalue()) <= max_bytes:
            return buf.getvalue()
        scale *= 0.8
    return image_bytes

def describe(client, img_bytes, folder_name):
    b64 = base64.standard_b64encode(img_bytes).decode()
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        messages=[{"role":"user","content":[
            {"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}},
            {"type":"text","text":f"""Popiš fotografii v češtině. POUZE latinkou, nikdy ne azbuku.
Složka (nápověda): "{folder_name}"

Vrať POUZE JSON:
{{"popis":"1-2 věty co vidíš","obsah":"jedna z: Moře / Pobřeží|Vodopád|Řeka / Jezero|Hory / Krajina|Příroda / Rostliny|Mořský život|Zvíře|Původní obyvatelé|Lidé — portréty|Lidé|Jídlo / Trh|Památky / Ruiny|Město / Architektura|Loď / Plavba|Mapa / Dokument|Ostatní","druh":null,"kat":"Senidub, Panama — Guna Yala"}}"""}
        ]}]
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"): text = text[4:]
    return json.loads(text.strip())

def main():
    katalog = json.loads(KATALOG_FILE.read_text(encoding="utf-8"))
    existing = {r["id"] for r in katalog}
    print(f"📚 Katalog: {len(katalog)} fotek")

    service = get_service()
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Najdi všechny soubory v Senidub
    resp = service.files().list(
        q=f"'{SENIDUB_ID}' in parents and trashed=false",
        fields="files(id,name,mimeType,size)",
        pageSize=200
    ).execute()

    files = [f for f in resp.get("files",[])
             if f["id"] not in existing
             and int(f.get("size",0)) > 10000]

    print(f"🆕 Nalezeno {len(files)} nových fotek v Senidub")

    added = 0
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f['name']}...", end=" ", flush=True)
        try:
            img = get_thumbnail(f["id"])
            if not img:
                print("⚠️  thumbnail nedostupný")
                continue
            img = resize(img)
            result = describe(client, img, "Senidub")
            katalog.append({
                "id": f["id"],
                "popis": result.get("popis",""),
                "kat": result.get("kat","Senidub, Panama — Guna Yala"),
                "obsah": result.get("obsah","Ostatní"),
                "gps": "9.583300,-78.966700",
                "druh": result.get("druh"),
                "nazev": f["name"],
                "zdroj": "vlastní",
                "heic": f["name"].lower().endswith(".heic"),
                "video": f["mimeType"].startswith("video/"),
                "misto": "Senidub, Panama — Guna Yala"
            })
            added += 1
            print(f"✅ {result.get('obsah','?')}")
        except Exception as e:
            print(f"⚠️  {e}")

    KATALOG_FILE.write_text(json.dumps(katalog, ensure_ascii=False, separators=(',',':')), encoding="utf-8")
    print(f"\n✅ Hotovo! Přidáno {added} fotek")

    if added > 0:
        os.chdir(Path(__file__).parent)
        subprocess.run(["git","add","katalog_data.json"])
        subprocess.run(["git","commit","-m",f"Senidub: přidáno {added} fotek"])
        subprocess.run(["git","push"])
        print("✅ GitHub aktualizován!")

if __name__ == "__main__":
    main()
