const APPS_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbzOYTX0w_1W7CBWS1ibGoqMzjnE89iKMazJz72SmfKuSpCE_R9mDLK5AeB4LJd4t9HS/exec';

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  try {
    const response = await fetch(APPS_SCRIPT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify(req.body),
      redirect: 'follow'
    });
    const text = await response.text();
    try {
      res.json(JSON.parse(text));
    } catch {
      res.json({ ok: true });
    }
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
};
