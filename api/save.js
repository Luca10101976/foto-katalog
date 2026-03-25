import fs from 'fs';
import path from 'path';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const edit = req.body;
    const dataPath = path.join(process.cwd(), 'public', 'katalog_data.json');
    const data = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));

    const idx = data.findIndex(r => r.id === edit.id);
    if (idx === -1) {
      return res.status(404).json({ error: 'Záznam nenalezen' });
    }

    data[idx] = {
      ...data[idx],
      popis: edit.popis,
      kat:   edit.kat,
      obsah: edit.obsah,
      gps:   edit.gps,
      druh:  edit.druh,
      zdroj: edit.zdroj,
      nazev: edit.nazev
    };

    fs.writeFileSync(dataPath, JSON.stringify(data, null, 0));
    res.status(200).json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
