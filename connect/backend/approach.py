"""One aerial approach tool; coordinates come from the hackathon model catalog."""
import csv
import io
import os
import re
from pathlib import Path
from PIL import Image
from . import describe, gemini

FAILURE = "I can't get an aerial view right now. Please try again shortly."
PROMPT = ("This is an aerial image of {address}. A blind person is arriving on foot from the street. "
          "In 2–3 short sentences, describe how to get from the sidewalk to the main entrance: "
          "which side the entrance is on, and what lies between (driveway, path, steps, parking). "
          "Use left/right and rough distances. Say this is based on aerial imagery and may not reflect current conditions.")


def normalized(text):
    return re.sub(r'[^a-z0-9]', '', text.lower().replace('avenue','ave').replace('street','st'))


def locate(address):
    """Exact street-address match; never silently substitute a nearby property."""
    target = normalized(address.split(',')[0])
    if not target or not re.match(r'\d', target):
        raise ValueError('A street address is required')
    path = Path(os.environ.get('MERIDIES_MODEL_INDEX') or gemini.KIT / 'models_index.csv')
    if path.exists():
        with path.open(encoding='utf-8-sig', newline='') as f:
            matches = [r for r in csv.DictReader(f)
                       if normalized(r.get('address','').split(',')[0]) == target
                       and r.get('lat') and r.get('lon')]
        # The demo catalog can have several scans of one property; require one city.
        if matches and len({(r.get('city','').lower(),r.get('state','').lower()) for r in matches}) == 1:
            row = matches[0]
            requested_city = address.split(',')[1].strip().lower() if ',' in address else ''
            if requested_city and requested_city != row.get('city','').strip().lower():
                raise ValueError('Address city does not match the catalog')
            return float(row['lat']), float(row['lon'])
    # Packaged demo contains only its mapped buildings, not the full catalog.
    for bid in describe.BUILDINGS:
        building = describe.load_plan(bid)['building']
        if normalized(building.get('name','')) == target and building.get('lat') and building.get('lon'):
            if ',' in address:
                raise ValueError('Full address cannot be verified without the model catalog')
            return building['lat'], building['lon']
    raise ValueError('Address is not in the available model catalog')


def describe_approach(address):
    try:
        if not gemini.secret('VEXCEL_API_KEY'):
            return FAILURE
        lat, lon = locate(address)
        image = describe.aerial_crop(lat, lon, half=60, px=1200)
        Image.open(io.BytesIO(image)).verify()
        text = gemini.generate([
            {'text': PROMPT.format(address=address) +
             '\nThe target property is centered in this north-up image, spanning about 120 metres. '
             'Define left and right from the sidewalk while facing the property. '
             'Use straight ahead, left or right, never compass directions. '
             'If the street-facing side cannot be determined, do not give a directional route. '
             'If the entrance or steps are obscured, say you cannot confirm them; do not invent a route. '
             'Keep the entire description under 50 words so the following spoken offer is not cut off.'},
            gemini.image_part(image)], model='gemini-3.8-flash', fallback=False, retries=1, timeout=35)
        return text.strip() or FAILURE
    except Exception:
        # No service errors or secrets reach the speech output.
        return FAILURE
