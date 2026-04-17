# MyCareernet Job Scraper

Scraper für [mycareernet.co](https://mycareernet.co/mycareernet/jobs) → statisches HTML (GitHub Pages).

## API

```
POST https://mycareernet.co/py/crpo/portaljobs/candidate/api/v1/getAll/
Content-Type: application/json
```

## Lokale Verwendung

```bash
pip install requests playwright
python -m playwright install chromium

# 1. Einmalig: echten Payload per Browser-Interception entdecken
python scraper.py --discover

# 2. Danach direkt Jobs scrapen
python scraper.py --keyword "BASF" --location "India" --pages 5

# Output: index.html + jobs_raw.json
```

## Wie der Discover-Modus funktioniert

1. Playwright öffnet `mycareernet.co/mycareernet/jobs` headless
2. Alle ausgehenden POST-Requests werden abgehört
3. Sobald ein Request an `/getAll/` geht, wird der exakte JSON-Body gespeichert
4. Dieser Body wird als `discovered_payload.json` gecacht
5. Danach: direkte API-Calls ohne Browser

## GitHub Actions

- Läuft täglich 06:00 UTC
- Commitet `index.html` automatisch
- GitHub Pages zeigt das Ergebnis an

## Dateien

| Datei | Beschreibung |
|---|---|
| `scraper.py` | Hauptskript |
| `discovered_payload.json` | Gecachter API-Payload (nach erstem Discover) |
| `index.html` | Generiertes statisches HTML |
| `jobs_raw.json` | Rohdaten der letzten 5 Jobs (Debug) |
