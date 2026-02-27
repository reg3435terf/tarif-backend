#!/usr/bin/env python3
"""
Tarifierungstool Backend – Sichere Groq-API-Proxy + Klassifizierungslogik.
Deployed auf Render.com als Web Service.
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import json, os, re, time, urllib.request, urllib.error, urllib.parse

app = Flask(__name__)
CORS(app)

# ── API Key (aus Render Environment Variable) ──
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── BAZG Cache Pfad ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'bazg_cache')

# ── Allgemeine Vorschriften & CH-Vorschriften (vollständig) ──
AV_TEXT = """═══ ALLGEMEINE VORSCHRIFTEN FÜR DIE EINREIHUNG (AV) ═══

AV 1 – VORRANG DES WORTLAUTS:
Massgebend für die Einreihung sind der Wortlaut der Nummern und der Abschnitt- oder Kapitel-Anmerkungen sowie – soweit Nummern oder Anmerkungen nichts anderes vorschreiben – die nachstehenden Vorschriften.

AV 2a – UNVOLLSTÄNDIGE WAREN:
Die Einreihung unvollständiger oder unfertiger Waren folgt der Einreihung vollständiger oder fertiger Waren, sofern sie im vorliegenden Zustand die wesentlichen Merkmale der vollständigen oder fertigen Ware besitzen. Zerlegte oder nicht zusammengesetzte vollständige/fertige Waren werden wie zusammengesetzte behandelt.

AV 2b – MISCHUNGEN UND GEMISCHE:
Die Erwähnung eines Stoffes in einer Nummer gilt auch für Mischungen oder Gemische dieses Stoffes mit anderen Stoffen. Die Einreihung von Mischungen erfolgt nach AV 3.

AV 3 – MEHRERE MÖGLICHE NUMMERN:
a) Die Nummer mit der genaueren Warenbezeichnung hat Vorrang vor Nummern mit allgemeiner Beschreibung.
b) Mischungen, Kombinationswaren und Waren, die aus verschiedenen Stoffen bestehen: nach der Ware, die den wesentlichen Charakter bestimmt.
c) Lässt sich die Einreihung nicht nach AV 3a oder 3b bestimmen: die letzte in Betracht kommende Nummer.

AV 4 – NICHT EINREIHBARE WAREN:
Waren, die sich mit den vorstehenden Vorschriften nicht einreihen lassen, werden nach den Waren eingereiht, denen sie am ähnlichsten sind.

AV 5 – BEHÄLTNISSE UND VERPACKUNGEN:
Behältnisse (Etuis, Futteral, Kästen usw.), die speziell für eine bestimmte Ware geformt sind, für längeren Gebrauch geeignet und zusammen mit der Ware eingeführt werden: Einreihung mit der Ware. Verpackungen: gleichfalls mit der enthaltenen Ware, ausser wenn die Verpackung dem Ganzen ihren wesentlichen Charakter verleiht.

AV 6 – UNTERNUMMERN-EINREIHUNG:
Die Einreihung in die Unternummern einer Nummer richtet sich nach dem Wortlaut dieser Unternummern und der Unternummern-Anmerkungen sowie mutatis mutandis nach den Vorschriften AV 1–5. Nur Unternummern der gleichen Gliederungsstufe sind vergleichbar.

═══ SCHWEIZERISCHE VORSCHRIFTEN (CHV) ═══

CHV 1: Für die Einreihung in Schweizer Unternummern gelten die AV sinngemäss.
CHV 2: Gebrauchte Waren: gleicher Zollansatz wie neue Waren, ausser besondere Bestimmungen.
CHV 3: Stückgewicht = Eigengewicht der Ware ohne Umschliessung.
CHV 4: Behältnis = unmittelbare Umschliessung der Ware.

═══ MWST-SÄTZE SCHWEIZ (seit 1.1.2024) ═══
- Normalsatz 8.1%: Alkohol, Tabak, Elektronik, Maschinen, Fahrzeuge, Kosmetik, Kleidung, Industrieprodukte, alle nicht explizit reduzierten Waren
- Reduzierter Satz 2.6%: Lebensmittel (Kap. 1-24 sofern nicht Alkohol/Tabak), nicht-alkoholische Getränke (Wasser, Saft, Softdrinks, Limonaden, Kaffee, Tee), Bücher/Zeitungen, Medikamente, Pflanzen
- Sondersatz 3.8%: AUSSCHLIESSLICH Beherbergungsleistungen (Hotelübernachtungen)

═══ ZOLLANSÄTZE ═══
- Kap. 1-24 (Agrarprodukte): Zollansätze variieren stark, gemäss Tarif
- Kap. 25-97 (Industrieprodukte): seit 1.1.2024 weitgehend zollfrei (0 CHF)"""


def call_groq(messages, max_tokens=2000, temperature=0.1, _retry=True):
    """Groq API call with automatic 429 retry."""
    payload = json.dumps({
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"}
    }).encode("utf-8")

    req = urllib.request.Request(GROQ_URL, data=payload, headers={
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Tarifierungstool/4.0"
    })

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retry:
            # Rate limit: wait and retry once
            retry_after = int(e.headers.get('Retry-After', 15))
            time.sleep(min(retry_after, 20))
            return call_groq(messages, max_tokens, temperature, _retry=False)
        raise Exception(f"HTTP Error {e.code}: {e.reason}")


# ── Open Food Facts lookup ──
def search_openfoodfacts(query):
    clean = re.sub(r'\D', '', query)
    if len(clean) >= 8:
        result = off_by_barcode(clean)
        if result:
            return result
    return off_text_search(query)


def off_by_barcode(ean):
    try:
        url = f"https://world.openfoodfacts.org/api/v2/product/{ean}.json?fields=product_name,brands,ingredients_text,categories,quantity"
        req = urllib.request.Request(url, headers={"User-Agent": "Tarifierungstool/4.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == 1 and data.get("product"):
                return format_off_product(data["product"], ean)
    except Exception:
        pass
    return None


def off_text_search(query):
    result = _off_search(query)
    if result:
        return result
    clean = re.sub(r'[\d,]+\s*(ml|l|g|kg|cl|dl)\b', '', query, flags=re.IGNORECASE).strip()
    if clean != query:
        result = _off_search(clean)
        if result:
            return result
    words = query.lower().split()
    if len(words) >= 2:
        for i in range(len(words) - 1, 0, -1):
            product_part = ' '.join(words[i:])
            brand_part = ' '.join(words[:i])
            if len(product_part) > 3:
                result = _off_search(f"{brand_part} {product_part}")
                if result:
                    return result
    return None


def _off_search(query):
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={encoded}&search_simple=1&action=process&json=1&page_size=3&fields=product_name,brands,ingredients_text,categories,quantity,code"
        req = urllib.request.Request(url, headers={"User-Agent": "Tarifierungstool/4.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            products = data.get("products", [])
            for p in products:
                if p.get("ingredients_text"):
                    return format_off_product(p, p.get("code", ""))
            if products:
                return format_off_product(products[0], products[0].get("code", ""))
    except Exception:
        pass
    return None


def format_off_product(product, ean=""):
    ingredients = product.get("ingredients_text", "") or ""
    if "Zutaten:" in ingredients:
        de_start = ingredients.index("Zutaten:")
        for marker in ["Ingrédients:", "Ingredienti:", "Ingredients:"]:
            if marker in ingredients[de_start + 10:]:
                de_end = ingredients.index(marker, de_start + 10)
                ingredients = ingredients[de_start:de_end].strip().rstrip(',')
                break
        else:
            ingredients = ingredients[de_start:].strip()
    elif len(ingredients) > 600:
        ingredients = ingredients[:600]
    return {
        "name": product.get("product_name", ""),
        "brand": product.get("brands", ""),
        "ingredients": ingredients,
        "categories": product.get("categories", ""),
        "quantity": product.get("quantity", ""),
        "ean": ean,
        "source": "Open Food Facts"
    }


# ── Web Search Fallback (Groq Compound) ──
def web_search_product(query):
    try:
        search_prompt = (
            f"Suche im Internet nach dem Produkt: '{query}'.\n"
            f"Finde folgende zollrelevante Informationen:\n"
            f"- Exakter Produktname und Marke\n"
            f"- Zusammensetzung / Zutaten / Material (mit Prozentangaben wenn verfügbar)\n"
            f"- Menge/Gewicht\n"
            f"- Produktkategorie\n"
            f"- Verwendungszweck\n\n"
            f"Antworte NUR als JSON:\n"
            f'{{"name": "...", "brand": "...", "ingredients": "...", "categories": "...", '
            f'"quantity": "...", "description": "...", "search_url": "..."}}'
        )
        payload = json.dumps({
            "model": "groq/compound",
            "messages": [
                {"role": "system", "content": "Du bist ein Produktrecherche-Assistent. Suche im Web nach dem angegebenen Produkt und extrahiere zollrelevante Daten. Antworte ausschliesslich als JSON."},
                {"role": "user", "content": search_prompt}
            ],
            "max_tokens": 1000,
            "temperature": 0.1
        }).encode("utf-8")

        req = urllib.request.Request(GROQ_URL, data=payload, headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Tarifierungstool/4.0"
        })

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                result = json.loads(content)
            name = result.get("name", "").strip()
            ingredients = result.get("ingredients", "").strip()
            if name or ingredients:
                return {
                    "name": name or query,
                    "brand": result.get("brand", "").strip(),
                    "ingredients": ingredients,
                    "categories": result.get("categories", "").strip(),
                    "quantity": result.get("quantity", "").strip(),
                    "description": result.get("description", "").strip(),
                    "ean": "",
                    "source": "Web-Suche",
                    "search_url": result.get("search_url", "")
                }
    except Exception:
        pass
    return None


# ── BAZG document reading ──
def read_cache_file(filename):
    path = os.path.join(CACHE_DIR, filename)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    return None


def get_chapter_docs(chapter_nums):
    """Load erl + anm for one or multiple chapters. Returns dict with labeled texts."""
    if isinstance(chapter_nums, int):
        chapter_nums = [chapter_nums]
    result = {}
    for ch_num in chapter_nums:
        ch = str(ch_num).zfill(2)
        erl = read_cache_file(f"erl_{ch}.txt")
        anm = read_cache_file(f"anm_{ch}.txt")
        if erl:
            result[f"erl_{ch}"] = erl
        if anm:
            result[f"anm_{ch}"] = anm
    return result


def extract_position_section(full_text, target_position, intro_chars=1500, max_section=7000):
    """
    Extrahiert aus dem vollen Erläuterungs-Text nur:
    1. Die Kapiteleinleitung (intro_chars Zeichen)
    2. Den Abschnitt zur target_position (z.B. '2202' oder '2009')
       → von 'XXXX.' bis zur nächsten '####.' Überschrift
    3. Tabellen (Mindestgehalt) werden nicht abgeschnitten

    Das reduziert 50k-Zeichen-Kapitel auf ~8-10k relevante Zeichen.
    """
    # Kapiteleinleitung
    intro = full_text[:intro_chars]

    if not target_position:
        return intro

    pos_str = str(target_position)

    # Suche die Startposition des Abschnitts (z.B. "2202.")
    start_patterns = [
        rf'^\s{{0,8}}{re.escape(pos_str)}\.',
        rf'^\s{{0,8}}{re.escape(pos_str)}\s',
        rf'\n{re.escape(pos_str)}\.'
    ]
    start = -1
    for pat in start_patterns:
        m = re.search(pat, full_text, re.MULTILINE)
        if m:
            start = m.start()
            break

    if start < 0:
        # Position nicht gefunden – gib Einleitung + erste 5k zurück
        return intro + '\n' + full_text[intro_chars:intro_chars + 5000]

    # Suche das Ende des Abschnitts (nächste 4-stellige Position)
    next_pos_pattern = re.compile(
        rf'^\s{{0,8}}\d{{4}}[\.\s]',
        re.MULTILINE
    )
    end = len(full_text)
    for m in next_pos_pattern.finditer(full_text, start + 10):
        candidate = full_text[m.start():m.start() + 6].strip()
        # Nur wenn es eine ANDERE Position ist (nicht Unterposition)
        if candidate[:4] != pos_str:
            end = m.start()
            break

    section = full_text[start:end]

    # Sicherheits-Truncation bei sehr langen Abschnitten
    if len(section) > max_section:
        # Behalte immer die Tabellen (Mindestgehalt etc.)
        table_idx = section.lower().find('mindestgehalt')
        if 0 < table_idx < max_section - 2000:
            # Tabelle einschliessen, danach kürzen
            section = section[:max_section]
        else:
            section = section[:max_section]

    # Duplikate im Intro vermeiden
    if start < intro_chars:
        return full_text[start:start + len(intro) + len(section)]

    return intro + '\n\n' + section


# ── Chapter detection ──
CHAPTER_KEYWORDS = {
    1:  ['lebende tiere', 'schlachtvieh', 'rind', 'schwein', 'geflügel'],
    2:  ['fleisch', 'schlachtnebenerzeugnisse', 'hackfleisch', 'steak'],
    3:  ['fisch', 'lachs', 'thunfisch', 'krebs', 'garnele', 'muschel', 'weichtier'],
    4:  ['milch', 'joghurt', 'käse', 'butter', 'sahne', 'eier', 'honig', 'rahm'],
    5:  ['tierische erzeugnisse', 'knochen', 'horn', 'tierhaare'],
    6:  ['pflanzen', 'blumen', 'zwiebeln', 'setzling', 'strauch'],
    7:  ['gemüse', 'kartoffel', 'tomate', 'gurke', 'salat', 'karotte', 'zwiebel'],
    8:  ['früchte', 'nüsse', 'zitrusfrüchte', 'banane', 'apfel', 'orange', 'erdbeere', 'kirsche'],
    9:  ['kaffee', 'tee', 'mate', 'gewürz', 'pfeffer', 'zimt', 'vanille'],
    10: ['getreide', 'reis', 'weizen', 'mais', 'hafer', 'gerste', 'roggen'],
    11: ['mehl', 'stärke', 'malz', 'müllerei', 'grieß', 'kleie'],
    12: ['ölsaat', 'sojabohnen', 'raps', 'sonnenblumenkerne', 'leinsaat'],
    13: ['schellack', 'pflanzengummi', 'harz', 'pektin'],
    14: ['flechtstoffe', 'bambus', 'korbweiden', 'bast'],
    15: ['fette', 'öle', 'olivenöl', 'palmöl', 'margarine', 'schmalz'],
    16: ['wurst', 'fleischzubereitung', 'fischkonserve', 'pastete'],
    17: ['zucker', 'zuckerrohr', 'rübenzucker', 'melasse', 'glucose', 'fructose'],
    18: ['kakao', 'schokolade', 'kakaomasse', 'kakaobutter'],
    19: ['backwaren', 'brot', 'teigwaren', 'pizza', 'pasta', 'nudeln', 'kekse', 'gebäck', 'waffeln', 'müsli', 'cornflakes'],
    # Kap. 20: Fruchtsäfte (2009), Konserven, Konfitüren, Zubereitungen aus Früchten/Gemüse
    20: ['fruchtsaft', 'gemüsesaft', 'direktsaft', 'fruchtnektar', 'nektar',
         'orangensaft', 'apfelsaft', 'traubensaft', 'tomatensaft', 'zitronensaft',
         'konfitüre', 'marmelade', 'fruchtzubereitung', 'gemüsekonserve',
         '100% saft', '100% frucht', 'pure juice'],
    21: ['suppe', 'brühe', 'sauce', 'senf', 'mayonnaise', 'hefe', 'suppenwürze',
         'nahrungsergänzung', 'vitamin', 'mineral supplement', 'gewürzsauce'],
    # Kap. 22: Getränke (NICHT reiner Fruchtsaft!) – Wasser, Bier, Wein, Spirituosen, Softdrinks
    22: ['getränk', 'mineralwasser', 'tafelwasser', 'limonade', 'cola', 'fanta',
         'bier', 'wein', 'sekt', 'champagner', 'spirituosen', 'whisky', 'vodka',
         'energy drink', 'red bull', 'rivella', 'eistee', 'kombucha',
         'alkohol', 'schnaps', 'likör', 'essig', 'softdrink'],
    23: ['futtermittel', 'tierfutter', 'hundefutter', 'katzenfutter', 'kleie futter'],
    24: ['tabak', 'zigaretten', 'zigarre', 'snus', 'e-zigarette', 'rauchtabak'],
    25: ['salz', 'schwefel', 'gips', 'kalkstein', 'zement', 'sand', 'kies', 'quarz'],
    27: ['erdöl', 'mineralöl', 'benzin', 'diesel', 'heizöl', 'kerosin', 'kohle'],
    28: ['anorganische chemikalien', 'säuren', 'laugen', 'oxide', 'chlor', 'phosphor'],
    29: ['organische chemikalien', 'kohlenwasserstoff', 'alkohol organisch', 'ester'],
    30: ['arzneimittel', 'medikament', 'pharmazeutisch', 'tabletten', 'arznei', 'impfstoff'],
    33: ['parfum', 'kosmetik', 'shampoo', 'seife', 'creme', 'lotion', 'lippenstift', 'deo'],
    34: ['waschmittel', 'reinigungsmittel', 'spülmittel', 'geschirrspüler'],
    39: ['kunststoff', 'plastik', 'polymer', 'polyethylen', 'pvc', 'polyester'],
    40: ['kautschuk', 'gummi', 'latex', 'reifen'],
    42: ['leder', 'handtasche', 'koffer', 'brieftasche', 'geldbörse', 'rucksack'],
    44: ['holz', 'holzkohle', 'parkett', 'holzplatte', 'möbelholz'],
    48: ['papier', 'karton', 'pappe', 'papierverpackung'],
    49: ['buch', 'zeitung', 'zeitschrift', 'druck', 'atlas', 'kalender'],
    61: ['bekleidung gewirkt', 'strickjacke', 'pullover', 'shirt', 'unterwäsche gewirkt'],
    62: ['bekleidung gewebt', 'hose', 'jacke', 'mantel', 'anzug', 'hemd', 'kleid', 'jeans'],
    63: ['textilwaren', 'decke', 'vorhang', 'bettwäsche', 'handtuch', 'teppich'],
    64: ['schuhe', 'stiefel', 'sandalen', 'turnschuhe', 'pumps'],
    65: ['kopfbedeckung', 'hut', 'mütze', 'helm', 'kappe'],
    69: ['keramik', 'porzellan', 'steingut', 'fliesen'],
    70: ['glas', 'glasflasche', 'glaswaren', 'spiegel'],
    71: ['schmuck', 'gold', 'silber', 'platin', 'edelstein', 'diamant', 'perle', 'münzen edelmetall'],
    72: ['eisen', 'stahl', 'roheisen', 'stahlblech', 'edelstahl'],
    73: ['schrauben', 'nägel', 'stahlrohre', 'stahlwaren', 'eisenwaren', 'werkzeugstahl'],
    76: ['aluminium', 'aluminiumfolie', 'aluminiumlegierung'],
    82: ['werkzeuge', 'hammer', 'zange', 'säge', 'messer', 'schere', 'bohrer', 'schraubenzieher'],
    83: ['schlösser', 'schlüssel', 'beschläge', 'metallwaren haushalt'],
    84: ['maschinen', 'motoren', 'pumpen', 'kühlschrank', 'waschmaschine', 'drucker', 'computer', 'laptop', 'notebook', 'server'],
    85: ['elektrisch', 'telefon', 'handy', 'smartphone', 'iphone', 'fernseher', 'kabel', 'batterie', 'akku', 'led lampe', 'kopfhörer', 'lautsprecher', 'playstation', 'konsole'],
    87: ['auto', 'pkw', 'lkw', 'motorrad', 'fahrrad', 'velo', 'elektroauto', 'bus'],
    88: ['flugzeug', 'drohne', 'hubschrauber', 'rakete'],
    89: ['schiff', 'boot', 'yacht', 'kahn'],
    90: ['optik', 'kamera', 'brille', 'mikroskop', 'teleskop', 'messgerät', 'thermometer'],
    91: ['uhr', 'armbanduhr', 'wanduhr', 'uhrmacher'],
    92: ['musikinstrument', 'gitarre', 'klavier', 'geige', 'flöte', 'schlagzeug'],
    93: ['waffen', 'gewehr', 'pistole', 'munition'],
    94: ['möbel', 'stuhl', 'tisch', 'bett', 'schrank', 'sofa', 'matratze', 'leuchte'],
    95: ['spielzeug', 'spiele', 'sportgeräte', 'ball', 'puppe', 'lego'],
    96: ['kugelschreiber', 'bleistift', 'bürste', 'kamm', 'feuerzeug', 'regenschirm'],
    97: ['kunstwerk', 'gemälde', 'skulptur', 'antiquität'],
}

# Schlüsselwörter die KLAR auf reinen Fruchtsaft (Kap. 20, Nr. 2009) hinweisen
JUICE_INDICATORS = {
    'fruchtsaftkonzentrat', 'fruchtsäfte', 'direktsaft', '100% saft',
    'orangensaft', 'apfelsaft', 'traubensaft', 'tomatensaft',
    'grapefruitsaft', 'ananassaft', 'zitronensaft', 'mangosaft',
    'fruchtnektar', 'gemüsesaft', 'pure juice', '100% frucht',
    'hohes c', 'tropicana', 'granini', 'minute maid', 'eckes granini',
    'capri-sonne', 'capri sonne', 'innocent', 'pfanner saft'
}

# Schlüsselwörter die klar auf Softdrink/Getränk (Kap. 22) hinweisen
DRINK_INDICATORS = {
    'limonade', 'mineralwasser', 'tafelwasser', 'softdrink', 'cola',
    'energy drink', 'bier', 'wein', 'rivella', 'eistee', 'kombucha',
    'kohlensäure', 'aromatisiert', 'tafelgetränk'
}

# Regex für Flüssigkeitsmengen → starker Hinweis auf Getränk
LIQUID_VOLUME_RE = re.compile(
    r'\b(?:(?:[0-9]+(?:[.,][0-9]+)?)\s*(?:ml|cl|dl|l(?:iter)?)\b)',
    re.IGNORECASE
)


def detect_chapters(query, product_info):
    """
    Bestimmt das primäre Kapitel und ob ein zweites Kapitel geprüft werden muss.
    Gibt (primary_chapter, extra_chapters) zurück.
    """
    text = query.lower()
    if product_info:
        text += ' ' + (
            product_info.get('categories', '') + ' ' +
            product_info.get('name', '') + ' ' +
            product_info.get('ingredients', '')
        ).lower()

    # Spezialfall Getränke: Kap. 20 vs 22 unterscheiden
    has_juice = any(kw in text for kw in JUICE_INDICATORS)
    has_drink = any(kw in text for kw in DRINK_INDICATORS)

    # Flüssigkeitsmenge (z.B. "1L", "500ml") in der Anfrage → könnte Getränk sein
    has_liquid_volume = bool(LIQUID_VOLUME_RE.search(query))

    # Score-basierte Kapitelbestimmung
    scores = {}
    for ch, keywords in CHAPTER_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[ch] = score

    primary = None
    extra = []

    if scores:
        max_score = max(scores.values())
        top = [ch for ch, sc in scores.items() if sc == max_score]
        primary = max(top)  # höchste Nummer bei Gleichstand

    # Getränke-Grenzfall: Kap. 20 (2009 Säfte) und Kap. 22 beide laden
    if primary in (20, 22) or has_juice or has_drink:
        if has_juice and not has_drink:
            # Eher reiner Saft → primär Kap. 20, extra Kap. 22
            primary = 20
            extra = [22]
        elif has_drink and not has_juice:
            # Klares Getränk → primär Kap. 22
            primary = 22
            extra = []
        else:
            # Beides oder unklar → beide laden, primär per Score
            if primary not in (20, 22):
                primary = 22
            extra = [20] if primary == 22 else [22]
    elif has_liquid_volume and primary is not None and 1 <= primary <= 24:
        # Flüssigkeitsmenge erkannt, aber kein klarer Getränke-Hinweis:
        # Primäres Kapitel beibehalten + Kap. 20 & 22 als Alternativ-Check
        extra = [20, 22]

    return primary, extra


def guess_chapter_llm(query, product_info):
    """LLM-basierte Kapitelbestimmung als Fallback."""
    try:
        ingredients_hint = ""
        if product_info:
            ingredients_hint = (
                f"\nZutaten/Material: {product_info.get('ingredients', '')[:300]}"
                f"\nKategorien: {product_info.get('categories', '')}"
            )
        ch_result = call_groq([
            {"role": "system", "content": (
                "Bestimme das Kapitel (1-97) des Schweizer Zolltarifs für dieses Produkt. "
                "WICHTIG: Reiner Fruchtsaft = Kapitel 20 (Nr. 2009). "
                "Softdrinks/Limonaden/aromatisierte Getränke = Kapitel 22. "
                "Antworte als JSON: {\"chapter\": 22, \"reason\": \"...\", \"also_check\": [20]}"
            )},
            {"role": "user", "content": f"Produkt: {query}{ingredients_hint}"}
        ], max_tokens=300)
        primary = ch_result.get("chapter", 22)
        extra = ch_result.get("also_check", [])
        return primary, extra
    except Exception:
        return 22, []


# ── Classification Prompt ──
CLASSIFY_PROMPT = """Du bist ein zertifizierter Schweizer Zolltarif-Experte beim BAZG (Bundesamt für Zoll und Grenzsicherheit).
Deine Aufgabe: das unten beschriebene Produkt korrekt in den Schweizerischen Gebrauchstarif einreihen.

{av_section}

{docs_section}

{product_section}

═══ PFLICHTABLAUF – FOLGE DIESEN SCHRITTEN EXAKT ═══

SCHRITT 1 – PRODUKTIDENTIFIKATION:
Beschreibe das Produkt vollständig: Zusammensetzung, Verwendungszweck, Verarbeitungsgrad.

SCHRITT 2 – AV 1: KAPITEL UND POSITION BESTIMMEN:
- Lies die Anmerkungen zum betreffenden Kapitel/Abschnitt VOLLSTÄNDIG durch.
- Prüfe ob Ausschlussbestimmungen ("Nicht hierher gehören...") zutreffen.
- Bestimme die 4-stellige Position nach dem Wortlaut (AV 1).
- Bei Getränken: ZUERST prüfen ob Nr. 2009 (reiner Fruchtsaft, Kap. 20) zutrifft, DANN 2202!
  Entscheidungskriterium: Ist das Produkt ein reiner Saft (gepresst/aus Konzentrat, ohne wesentliche Zusätze)?
  → JA: 2009 (Kap. 20)
  → NEIN (aromatisiert, verdünnt mit Wasser+Zucker+Aroma, mit anderen Zusätzen): 2202 (Kap. 22)

SCHRITT 3 – AV 2/3 WENN NÖTIG:
Nur wenn AV 1 keine eindeutige Einreihung erlaubt: wende AV 2a, 2b, 3a, 3b, 3c in dieser Reihenfolge an.

SCHRITT 4 – AV 6 + CHV 1: UNTERPOSITION BESTIMMEN:
- Lies die Erläuterungen zu den relevanten Unterpositionen durch.
- Vergleiche nur Unterpositionen GLEICHER Gliederungsstufe.
- Bei 2202: Wende die Mindestgehalt-Quotienten-Methode NUMERISCH an:
  Für jede Fruchtart: Quotient = vorhandener_Saftanteil% / Mindestgehalt%
  Summe aller Quotienten >= 1.0 → 2202.9931/32/9969 (Fruchtsaftgetränk)
  Summe < 1.0 + Wasserbasis → 2202.1000 (aromatisiertes Tafelgetränk)
  Summe < 1.0 + andere Basis → 2202.9990

SCHRITT 5 – ZITIERE DEN ENTSCHEIDENDEN SATZ:
Zitiere WÖRTLICH den Satz aus den Erläuterungen oder Anmerkungen, der deine Einreihung begründet.

SCHRITT 6 – MWST UND ZOLL:
Bestimme MWST-Satz gemäss den oben aufgeführten Regeln. Begründe explizit.

═══ AUSGABE ═══
Antworte AUSSCHLIESSLICH als JSON (kein weiterer Text):
{{
  "product_identified": "Produktname und Marke",
  "product_description": "Vollständige zollrelevante Beschreibung",
  "material": "Zusammensetzung mit %-Angaben soweit bekannt",
  "category": "Warenkategorie",
  "chapter": <Zahl>,
  "chapter_name": "...",
  "position": "XXXX",
  "position_name": "vollständiger Wortlaut der Position",
  "tariff_number": "XXXX.XXXX",
  "tariff_description": "vollständiger Wortlaut der Unterposition",
  "decision_path": [
    {{"step": 1, "title": "Produktidentifikation", "detail": "..."}},
    {{"step": 2, "title": "AV 1 – Kapitel/Position", "detail": "Geprüfte Kapitel: X. Anmerkung X besagt: '...' → Position XXXX weil ..."}},
    {{"step": 3, "title": "AV 2/3 (falls angewandt)", "detail": "AV X angewandt weil: ... ODER 'Nicht angewandt, AV 1 reicht'"}},
    {{"step": 4, "title": "AV 6 + CHV 1 – Unterposition", "detail": "Erläuterungen zu XXXX besagen: '...' → Unterposition XXXX.XXXX. Quotienten-Berechnung: ..."}},
    {{"step": 5, "title": "Massgebende Rechtsgrundlage", "detail": "Wörtliches Zitat: '...' [Quelle: Erläuterungen/Anmerkungen Kap. X]"}},
    {{"step": 6, "title": "MWST und Zoll", "detail": "MWST X.X% weil: ... Zoll: ..."}}
  ],
  "legal_notes_consulted": ["Anmerkung X zu Kap. Y: '...'", "..."],
  "erlaeuterungen_zitat": "Wörtliches Zitat der entscheidenden Erläuterung",
  "duty_info": "...",
  "mwst_rate": "X.X%",
  "mwst_category": "...",
  "confidence": "high|medium|low",
  "confidence_reason": "...",
  "notes": "Hinweise auf fehlende Infos oder Grenzfälle",
  "keywords": ["...", "..."],
  "bazg_docs_used": true
}}"""


def build_prompt(av_text, docs, chapter, extra_chapters, product_data_str):
    """
    Baut den Klassifikations-Prompt auf.
    Token-Budget (Groq Free Tier: 6000 TPM):
      - AV-Text:       ~500 tokens  (2000 chars)
      - Prompt-Frame:  ~500 tokens  (2000 chars)
      - Produktdaten:  ~200 tokens  ( 800 chars)
      - Primär-ERL:   ~2000 tokens  (8000 chars)
      - Primär-ANM:   ~1000 tokens  (4000 chars)
      - Extra-ERL:     ~600 tokens  (2400 chars)  [nur die Vergleichs-Position]
      - Response:      ~1200 tokens
      ─────────────────────────────────────────
      TOTAL:          ~6000 tokens  ✓
    """
    doc_parts = []
    primary_ch = str(chapter).zfill(2)

    # Für jedes Kapitel die wichtigste/komplexeste Position für die Extraktion.
    # Die erste Position (XX01) ist oft einfaches Wasser/Basisware –
    # die zweite/dritte enthält die Erläuterungen und Tabellen.
    CHAPTER_MAIN_POSITION = {
        4:  "0401",  # Milch
        8:  "0811",  # Früchte tiefgekühlt (viele Unternummern)
        17: "1701",  # Zucker
        18: "1806",  # Schokolade
        19: "1905",  # Backwaren/Biscuits
        20: "2009",  # Fruchtsäfte
        21: "2106",  # Lebensmittelzubereitungen
        22: "2202",  # Getränke (NICHT 2201=reines Wasser)
        33: "3304",  # Kosmetik
        39: "3926",  # Kunststoffwaren
        61: "6109",  # T-Shirts etc.
        62: "6203",  # Herrenbekleidung
        84: "8471",  # Computer
        85: "8517",  # Telefone/Smartphones
        87: "8703",  # Pkw
        94: "9403",  # Möbel
        95: "9503",  # Spielzeug
    }
    primary_position = CHAPTER_MAIN_POSITION.get(chapter, str(chapter * 100 + 1))

    erl_primary = docs.get(f"erl_{primary_ch}")
    anm_primary = docs.get(f"anm_{primary_ch}")

    if erl_primary:
        # Kapiteleinleitung + den relevanten Positionsabschnitt
        # max_section=6000 stellt sicher, dass wir unter 6000 TPM (Groq Free Tier) bleiben
        erl_trimmed = extract_position_section(
            erl_primary,
            target_position=primary_position,
            intro_chars=1200,
            max_section=6000
        )
        doc_parts.append(f"═══ OFFIZIELLE ERLÄUTERUNGEN – KAPITEL {chapter} (Auszug) ═══\n{erl_trimmed}")
    else:
        doc_parts.append(
            f"═══ OFFIZIELLE ERLÄUTERUNGEN – KAPITEL {chapter} ═══\n"
            f"[Nicht im Cache. Klassifiziere nach AV und Fachwissen.]"
        )

    if anm_primary:
        doc_parts.append(
            f"═══ OFFIZIELLE ANMERKUNGEN – KAPITEL {chapter} ═══\n{anm_primary[:3000]}"
        )

    # Extra-Kapitel: nur die direkt konkurrierende Position extrahieren
    # z.B. für Kap 22 → erl_20 mit Position 2009
    EXTRA_POSITIONS = {
        20: "2009",   # Fruchtsäfte
        22: "2202",   # Getränke
        21: "2106",   # Lebensmittelzubereitungen
        19: "1901",   # Backwaren
        4:  "0401",   # Milcherzeugnisse
    }
    for extra_ch in extra_chapters:
        extra_str = str(extra_ch).zfill(2)
        erl_extra = docs.get(f"erl_{extra_str}")
        if erl_extra:
            target_pos = EXTRA_POSITIONS.get(extra_ch, str(extra_ch * 100 + 1))
            extra_section = extract_position_section(
                erl_extra,
                target_position=target_pos,
                intro_chars=500,
                max_section=2000
            )
            doc_parts.append(
                f"═══ VERGLEICH: ERLÄUTERUNGEN KAPITEL {extra_ch} – Position {target_pos} ═══\n"
                f"[WICHTIG: Prüfe zuerst ob das Produkt hier einzureihen ist!]\n"
                f"{extra_section}"
            )

    docs_section = '\n\n'.join(doc_parts)
    product_section = f"═══ PRODUKTDATEN ═══\n{product_data_str}"

    return CLASSIFY_PROMPT.format(
        av_section=av_text,
        docs_section=docs_section,
        product_section=product_section
    )


def classify_product(product_query):
    """Hauptpipeline für die Tarifierung."""

    # ── Schritt 1: Produktdaten ermitteln ──
    off_data = search_openfoodfacts(product_query)
    web_data = None
    data_source = "none"

    if off_data:
        data_source = "off"
    else:
        web_data = web_search_product(product_query)
        if web_data:
            data_source = "web"

    product_info = off_data or web_data

    # ── Schritt 2: Kapitel(n) bestimmen ──
    primary_chapter, extra_chapters = detect_chapters(product_query, product_info)

    if primary_chapter is None:
        primary_chapter, extra_chapters = guess_chapter_llm(product_query, product_info)

    # ── Schritt 3: BAZG-Dokumente laden ──
    all_chapters = [primary_chapter] + [c for c in extra_chapters if c != primary_chapter]
    docs = get_chapter_docs(all_chapters)

    # ── Schritt 4: Produktdaten-String aufbauen ──
    if product_info:
        source_label = (
            "Open Food Facts (offizielle Produktdaten)"
            if data_source == "off"
            else "Web-Suche (automatisch recherchiert)"
        )
        desc_line = f"Beschreibung: {product_info['description']}\n" if product_info.get("description") else ""
        product_data_str = (
            f"Anfrage: {product_query}\n"
            f"Produktname: {product_info.get('name', product_query)}\n"
            f"Marke: {product_info.get('brand', 'unbekannt')}\n"
            f"Menge/Gewicht: {product_info.get('quantity', 'unbekannt')}\n"
            f"Zutaten/Zusammensetzung: {product_info.get('ingredients', 'unbekannt')}\n"
            f"{desc_line}"
            f"Kategorien: {product_info.get('categories', 'unbekannt')}\n"
            f"EAN: {product_info.get('ean', 'unbekannt')}\n"
            f"Datenquelle: {source_label}"
        )
    else:
        product_data_str = (
            f"Anfrage: {product_query}\n"
            f"HINWEIS: Keine Produktdaten gefunden. Zusammensetzung und genaue Produktart unbekannt.\n"
            f"→ confidence auf 'low' oder 'medium' setzen und fehlende Informationen benennen."
        )

    # ── Schritt 5: Prompt aufbauen und LLM aufrufen ──
    prompt = build_prompt(AV_TEXT, docs, primary_chapter, extra_chapters, product_data_str)

    try:
        result = call_groq([
            {"role": "system", "content": prompt},
            {"role": "user", "content": (
                f"Tarifiere folgendes Produkt nach Schweizer Zolltarif:\n{product_query}\n\n"
                f"WICHTIG: Folge dem Pflichtablauf (Schritte 1-6). "
                f"Zitiere die massgebenden Erläuterungen wörtlich. "
                f"Prüfe zuerst die Kapitel-Anmerkungen auf Ausschlüsse."
            )}
        ], max_tokens=2000)
    except Exception as e:
        return {"error": f"LLM-Einreihung fehlgeschlagen: {e}"}

    # ── Metadaten ergänzen ──
    result["bazg_docs_used"] = True
    result["data_source"] = data_source
    result["off_data_used"] = data_source == "off"
    result["web_search_used"] = data_source == "web"
    result["chapters_loaded"] = all_chapters
    if product_info:
        result["_off_product"] = {
            "name": product_info.get("name", ""),
            "brand": product_info.get("brand", ""),
            "ean": product_info.get("ean", ""),
            "source": product_info.get("source", "")
        }

    return result


# ── Flask Routes ──

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Tarifierungstool Backend"})


@app.route('/ping', methods=['GET', 'POST'])
def ping():
    import sys
    return jsonify({"python": sys.version, "method": request.method, "ok": True})


@app.route('/classify', methods=['POST'])
def classify():
    try:
        if not GROQ_API_KEY:
            return jsonify({"error": "GROQ_API_KEY nicht konfiguriert"}), 500

        data = request.get_json()
        if not data or not data.get("product", "").strip():
            return jsonify({"error": "Kein Produkt angegeben"}), 400

        product_query = data["product"].strip()
        result = classify_product(product_query)

        if not isinstance(result, dict):
            return jsonify({"error": f"Unerwarteter Ergebnistyp: {type(result)}"}), 500

        if "error" in result:
            return jsonify(result), 500

        return jsonify(result)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return jsonify({"error": f"Interner Fehler: {type(e).__name__}: {e}", "traceback": tb}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
