#!/usr/bin/env python3
"""
Tarifierungstool Backend – Sichere Groq-API-Proxy + Klassifizierungslogik.
Deployed auf Render.com als Web Service.
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import json, os, re, urllib.request, urllib.error, urllib.parse

app = Flask(__name__)
CORS(app)  # Frontend darf von überall zugreifen

# ── API Key (aus Render Environment Variable) ──
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── BAZG Cache Pfad ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'bazg_cache')

# ── AV text ──
AV_TEXT = """ALLGEMEINE VORSCHRIFTEN (AV):

AV 1: Massgebend für die Einreihung sind der Wortlaut der Nummern und der Abschnitt- oder Kapitel-Anmerkungen.
AV 2a: Unvollständige/unfertige Waren mit wesentlichen Merkmalen → wie fertige Ware. Zerlegte → wie zusammengesetzte.
AV 2b: Erwähnung eines Stoffes gilt auch für Mischungen. Einreihung nach AV 3.
AV 3: a) Genauere Warenbezeichnung hat Vorrang b) Mischungen → nach wesentlichem Charakter c) letzte zutreffende Nummer
AV 4: Nicht einreihbare Waren → ähnlichste Waren.
AV 5: Behältnisse/Verpackungen wie enthaltene Waren.
AV 6: Unternummern nach Wortlaut + Unternummern-Anmerkungen, mutatis mutandis AV 1-5.

CHV 1: Für CH-Unternummern gelten AV sinngemäss.
CHV 2-4: Gebrauchte Waren = gleicher Zoll. Stückgewicht = Eigengewicht. Behältnis = unmittelbare Umschliessung.

MWST-SÄTZE SCHWEIZ (seit 1.1.2024):
- Normalsatz: 8.1%
- Reduzierter Satz: 2.6% → Lebensmittel, nicht-alkoholische Getränke, Fruchtsäfte, Wasser, Softdrinks, Bücher, Zeitungen, Medikamente, Pflanzen
- Sondersatz: 3.8% → nur Beherbergung/Hotellerie
- Alkoholische Getränke (>0.5% Vol) → 8.1%

ZOLLANSÄTZE:
- Kap. 1-24 (Agrar): Zollansätze gemäss Tarif (variieren stark)
- Kap. 25-97 (Industrie): seit 1.1.2024 weitgehend zollfrei (0 CHF)"""


def call_groq(messages, max_tokens=3000, temperature=0.1):
    """Groq API call."""
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

    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


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
    elif len(ingredients) > 500:
        ingredients = ingredients[:500]
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


# ── BAZG document reading (from local cache) ──
def get_chapter_docs(chapter_num):
    ch = str(chapter_num).zfill(2)
    texts = {}
    erl_file = os.path.join(CACHE_DIR, f"erl_{ch}.txt")
    if os.path.exists(erl_file):
        with open(erl_file, 'r') as f:
            texts["erlaeuterungen"] = f.read()
    anm_file = os.path.join(CACHE_DIR, f"anm_{ch}.txt")
    if os.path.exists(anm_file):
        with open(anm_file, 'r') as f:
            texts["anmerkungen"] = f.read()
    return texts


def extract_relevant_sections(full_text, product_keywords):
    lines = full_text.split('\n')
    sections = []
    current_section = []
    current_header = ''
    for line in lines:
        is_header = bool(re.match(r'^\s*(\d{4})', line)) or (len(line.strip()) > 5 and line.strip().isupper())
        if is_header and current_section:
            sections.append((current_header, '\n'.join(current_section)))
            current_section = [line]
            current_header = line.strip()
        else:
            current_section.append(line)
    if current_section:
        sections.append((current_header, '\n'.join(current_section)))

    result_parts = []
    if sections:
        result_parts.append(sections[0][1][:4000])

    priority_terms = {'mindestgehalt', 'quotient', 'fruchtsaft', 'fruchtmark', 'gemuesesaft',
                      'anmerkung', 'ausgenommen', 'einschliesslich'}
    keywords_lower = {k.lower() for k in product_keywords if len(k) > 3}

    for header, text in sections[1:]:
        text_lower = text.lower()
        relevance = sum(1 for term in keywords_lower if term in text_lower)
        has_priority = any(p in text_lower for p in priority_terms)
        if relevance >= 1 or has_priority:
            result_parts.append(text)

    return '\n\n'.join(result_parts)


# ── Classification prompt ──
CLASSIFY_PROMPT = """Du bist ein Schweizer Zolltarif-Experte beim BAZG. Tarifiere das folgende Produkt.

═══ ALLGEMEINE VORSCHRIFTEN ═══
{av_text}

═══ OFFIZIELLE ERLÄUTERUNGEN ZU KAPITEL {chapter} ═══
{erl_text}

═══ OFFIZIELLE ANMERKUNGEN ZU KAPITEL {chapter} ═══
{anm_text}

═══ PRODUKTDATEN ═══
{product_data}

═══ AUFGABE ═══
Tarifiere das Produkt AUSSCHLIESSLICH auf Basis der obigen offiziellen Dokumente und der Produktdaten.

KRITISCHE REGELN:
1. Wende die AV systematisch an (AV 1 → AV 6)
2. Zitiere relevante Anmerkungen und Erläuterungen WÖRTLICH
3. Tarifnummer IMMER im Format XXXX.XXXX (8 Ziffern, 1 Punkt nach 4. Stelle)
4. MWST KORREKT bestimmen:
   - 2.6% für: ALLE Lebensmittel, ALLE nicht-alkoholischen Getränke (Saft, Wasser, Softdrinks, Tafelgetränke), Bücher, Medikamente
   - 8.1% für: Alkoholische Getränke, Tabak, Industrieprodukte, Elektronik, Werkzeuge, sonstige Waren
   - 3.8% NUR für Beherbergung
5. Bei Getränken mit Fruchtsaft: IMMER Mindestgehalttabelle + Quotienten-Methode aus den Erläuterungen anwenden!
   - Berechne den Quotienten: Saftanteil / Mindestgehalt für jede Fruchtart
   - Summe der Quotienten >= 1 → Fruchtsaftgetränk (2202.9931/32, 9969)
   - Summe der Quotienten < 1 → UNTERSCHEIDE:
     a) Basis = WASSER + Zucker/Süssmittel + Aroma/Fruchtsaft → **2202.1000** (aromatisiertes Tafelgetränk, Limonade)
        Beispiele: Multivitaminsaft mit Wasser und 12% Saft, Limonaden, Cola, Eistee auf Wasserbasis
     b) Basis NICHT Wasser (z.B. Sojamilch, Energydrink mit Taurin/Koffein) → **2202.9990**
   WICHTIG: Die meisten handelsüblichen Fruchtgetränke/Multivitaminsäfte mit geringem Saftanteil sind Wasser-basiert → 2202.1000!
6. Falls Infos fehlen → confidence "medium", erkläre was fehlt

Antworte als JSON:
{{
  "product_identified": "Produktname und Marke",
  "product_description": "Beschreibung mit allen zollrelevanten Merkmalen",
  "material": "Zusammensetzung mit Anteilen",
  "category": "Warenkategorie",
  "chapter": {chapter},
  "chapter_name": "...",
  "position": "XXXX",
  "position_name": "...",
  "tariff_number": "XXXX.XXXX",
  "tariff_description": "...",
  "decision_path": [
    {{"step": 1, "title": "Produktidentifikation", "detail": "..."}},
    {{"step": 2, "title": "Anmerkungen geprüft", "detail": "Anmerkung X besagt: '...'"}},
    {{"step": 3, "title": "Erläuterungen konsultiert", "detail": "..."}},
    {{"step": 4, "title": "Positionsbestimmung (AV 1)", "detail": "..."}},
    {{"step": 5, "title": "Unterposition (AV 6)", "detail": "..."}},
    {{"step": 6, "title": "Zoll und MWST", "detail": "..."}}
  ],
  "legal_notes_consulted": ["..."],
  "duty_info": "...",
  "mwst_rate": "X.X%",
  "mwst_category": "...",
  "confidence": "high|medium|low",
  "confidence_reason": "...",
  "notes": "...",
  "keywords": ["...", "..."],
  "bazg_docs_used": true
}}"""


# ── Chapter determination ──
CHAPTER_KEYWORDS = {
    1: ['tiere', 'lebend'],
    2: ['fleisch', 'schlachtnebenerzeugnisse'],
    3: ['fisch', 'krebs', 'weichtier'],
    4: ['milch', 'eier', 'honig', 'kaese', 'butter', 'joghurt'],
    5: ['tierisch', 'knochen', 'horn'],
    6: ['pflanz', 'blumen', 'zwiebeln'],
    7: ['gemuese', 'kartoffel', 'tomate'],
    8: ['frucht', 'nuess', 'zitrus', 'banane', 'apfel', 'orange'],
    9: ['kaffee', 'tee', 'mate', 'gewuerz'],
    10: ['getreide', 'reis', 'weizen', 'mais', 'hafer'],
    11: ['muellerei', 'mehl', 'staerke', 'malz'],
    12: ['oelsaat', 'soja', 'raps'],
    13: ['schellack', 'gummi', 'harz'],
    14: ['flechtstoffe'],
    15: ['fett', 'oel', 'olivenoel', 'margarine'],
    16: ['wurst', 'fleischzubereitung', 'konserve'],
    17: ['zucker', 'suess'],
    18: ['kakao', 'schokolade'],
    19: ['backware', 'teigware', 'brot', 'pizza', 'pasta', 'nudel', 'keks', 'gebaeck'],
    20: ['gemusezubereitung', 'fruchtzubereitung', 'konfituere', 'marmelade'],
    21: ['suppe', 'sauce', 'senf', 'hefe', 'wuerze', 'nahrungsergaenzung'],
    22: ['getraenk', 'saft', 'wasser', 'bier', 'wein', 'alkohol', 'limonade', 'mineral', 'multivitamin', 'cola', 'energy', 'drink', 'tafelgetraenk', 'nektar'],
    23: ['futtermittel', 'tierfutter'],
    24: ['tabak', 'zigarett'],
    25: ['salz', 'schwefel', 'stein', 'gips', 'kalk', 'zement'],
    27: ['erdoel', 'mineraloel', 'benzin', 'diesel'],
    28: ['anorganisch', 'chemisch'],
    30: ['pharma', 'medikament', 'arznei', 'tablette', 'pille'],
    33: ['parfum', 'kosmetik', 'shampoo', 'seife', 'creme'],
    34: ['waschm', 'reinigung'],
    39: ['kunststoff', 'plastik'],
    40: ['kautschuk', 'gummi'],
    42: ['leder', 'tasche', 'koffer'],
    44: ['holz', 'holzkohle'],
    48: ['papier', 'karton', 'pappe'],
    49: ['buch', 'zeitung', 'druck'],
    61: ['bekleidung', 'kleid', 'shirt', 'hose', 'jacke', 'pullover', 'gewirk'],
    62: ['bekleidung', 'mantel', 'anzug', 'hemd'],
    63: ['textil', 'decke', 'vorhang'],
    64: ['schuh', 'stiefel', 'sandale'],
    69: ['keramik', 'porzellan'],
    70: ['glas', 'flasche'],
    71: ['schmuck', 'gold', 'silber', 'perle', 'edelstein'],
    72: ['eisen', 'stahl'],
    73: ['eisenware', 'stahlware', 'schraube', 'nagel'],
    76: ['aluminium'],
    82: ['werkzeug', 'hammer', 'zange', 'saege', 'messer', 'schere', 'bohrer'],
    83: ['schloss', 'schluessel', 'beschlag'],
    84: ['maschine', 'motor', 'pumpe', 'kuehlschrank', 'waschmaschine', 'computer', 'laptop', 'drucker'],
    85: ['elektr', 'telefon', 'handy', 'smartphone', 'fernseher', 'kabel', 'batterie', 'akku', 'lampe', 'led', 'kopfhoerer', 'lautsprecher', 'mikrofon'],
    87: ['auto', 'fahrzeug', 'pkw', 'lkw', 'motorrad', 'fahrrad', 'velo'],
    90: ['optik', 'kamera', 'brille', 'mikroskop', 'messgeraet'],
    91: ['uhr', 'uhren'],
    92: ['musikinstrument', 'gitarre', 'klavier'],
    94: ['moebel', 'stuhl', 'tisch', 'bett', 'schrank', 'lampe', 'matratze'],
    95: ['spielzeug', 'spiel', 'sport', 'ball'],
    96: ['kugelschreiber', 'stift', 'buerste', 'kamm'],
    97: ['kunst', 'antiquitaet', 'gemaelde'],
}


def guess_chapter(query, product_data=None):
    text = query.lower()
    if product_data:
        text += ' ' + (product_data.get('categories', '') + ' ' +
                       product_data.get('name', '')).lower()
    scores = {}
    for ch, keywords in CHAPTER_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[ch] = score
    if scores:
        max_score = max(scores.values())
        top_chapters = [ch for ch, sc in scores.items() if sc == max_score]
        if len(top_chapters) == 1:
            return top_chapters[0]
        return max(top_chapters)
    return None


def classify_product(product_query):
    """Main classification pipeline."""
    # Step 1: Try Open Food Facts
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

    # Step 2: Determine chapter
    chapter = guess_chapter(product_query, product_info)
    if chapter is None:
        try:
            ingredients_hint = ""
            if product_info:
                ingredients_hint = f"\nZutaten/Material: {product_info.get('ingredients', '')}"
            ch_result = call_groq([
                {"role": "system", "content": "Bestimme das Kapitel (1-97) des Schweizer Zolltarifs für dieses Produkt. Antworte als JSON: {\"chapter\": 22, \"reason\": \"...\"}"},
                {"role": "user", "content": f"Produkt: {product_query}{ingredients_hint}"}
            ], max_tokens=200)
            chapter = ch_result.get("chapter", 22)
        except Exception:
            chapter = 22

    # Step 3: Load BAZG documents
    docs = get_chapter_docs(chapter)
    erl_text = docs.get("erlaeuterungen", "[Erläuterungen nicht verfügbar]")
    anm_text = docs.get("anmerkungen", "[Anmerkungen nicht verfügbar]")

    product_keywords = product_query.split()
    if product_info:
        product_keywords += product_info.get('ingredients', '').split()[:20]
        product_keywords += product_info.get('name', '').split()

    max_chars = 24000
    if len(erl_text) > max_chars:
        erl_text = extract_relevant_sections(erl_text, product_keywords)[:max_chars]
    if len(anm_text) > max_chars:
        anm_text = anm_text[:max_chars]

    # Step 4: Build product data string
    if product_info:
        source_label = "Open Food Facts (echte Produktdaten)" if data_source == "off" else "Web-Suche (automatisch recherchiert)"
        desc_line = ""
        if product_info.get("description"):
            desc_line = f"Beschreibung: {product_info['description']}\n"
        product_data_str = (
            f"Produkt: {product_info.get('name', product_query)} ({product_info.get('brand', 'unbekannt')})\n"
            f"Menge: {product_info.get('quantity', 'unbekannt')}\n"
            f"Zutaten/Zusammensetzung: {product_info.get('ingredients', 'unbekannt')}\n"
            f"{desc_line}"
            f"Kategorien: {product_info.get('categories', 'unbekannt')}\n"
            f"EAN: {product_info.get('ean', 'unbekannt')}\n"
            f"Quelle: {source_label}"
        )
    else:
        product_data_str = (
            f"Produkt: {product_query}\n"
            f"HINWEIS: Keine Produktdaten gefunden. Zusammensetzung unbekannt.\n"
            f"Bitte confidence entsprechend tief setzen."
        )

    # Step 5: LLM classification
    prompt = CLASSIFY_PROMPT.format(
        av_text=AV_TEXT,
        chapter=chapter,
        erl_text=erl_text,
        anm_text=anm_text,
        product_data=product_data_str
    )

    try:
        result = call_groq([
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Tarifiere: {product_query}"}
        ], max_tokens=3000)
    except Exception as e:
        return {"error": f"LLM-Einreihung fehlgeschlagen: {e}"}

    # Add metadata
    result["bazg_docs_used"] = True
    result["data_source"] = data_source
    result["off_data_used"] = data_source == "off"
    result["web_search_used"] = data_source == "web"
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


@app.route('/classify', methods=['POST'])
def classify():
    if not GROQ_API_KEY:
        return jsonify({"error": "GROQ_API_KEY nicht konfiguriert"}), 500

    data = request.get_json()
    if not data or not data.get("product", "").strip():
        return jsonify({"error": "Kein Produkt angegeben"}), 400

    product_query = data["product"].strip()
    result = classify_product(product_query)

    if "error" in result:
        return jsonify(result), 500

    return jsonify(result)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
