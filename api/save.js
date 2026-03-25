const fs = require('fs');
const path = require('path');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }
  try {
    const edit = req.body;
    const dataPath = path.join(process.cwd(), 'katalog_data.json');
    const data = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));
    const idx = data.findIndex(r => r.id === edit.id);
    if (idx === -1) return res.status(404).json({ error: 'Nenalezeno' });
    data[idx] = { ...data[idx], ...edit };
    fs.writeFileSync(dataPath, JSON.stringify(data));
    res.status(200).json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
};
