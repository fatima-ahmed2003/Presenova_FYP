import json, os, re, sys, tempfile, struct, zlib
sys.path.insert(0, os.path.dirname(__file__))
from pptx import Presentation
from pptx.util import Inches
from universal_rewrite import run_pipeline, parse_edits, extract_units

TMP = tempfile.mkdtemp()


# ---------- build different decks (different topic/language/layout/size) ----------
def _png(path):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    def ch(t, d): c = struct.pack(">I", len(d)) + t + d; return c + struct.pack(">I", zlib.crc32(t + d))
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                           + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))

def add_slide(prs, title, bullets, layout=1):
    s = prs.slides.add_slide(prs.slide_layouts[layout])
    s.shapes.title.text = title
    tf = s.placeholders[1].text_frame
    tf.text = bullets[0]
    for b in bullets[1:]:
        tf.add_paragraph().text = b
    return s

def deck_business():
    p = Presentation()
    add_slide(p, "Q3  Marketing  Results", ["Revenue grew grew 12% in in Q3 2026", "we  launched 3 campaigns", "Customer retention is very very strong ."])
    add_slide(p, "Strategy", ["Focus on on enterprise clients", "Expand to 4 new regions", "basically reduce churn by 5%"])
    for i in range(4):
        add_slide(p, f"Section {i+1} overview", ["the the team shipped 2 features", "Really improve onboarding time"])
    f = os.path.join(TMP, "business.pptx"); p.save(f); return f

def deck_science():
    p = Presentation()
    s = add_slide(p, "Protein folding  basics", ["Proteins fold into into 3D shapes", "Folding takes  microseconds"])
    # multi-run bold paragraph
    para = s.placeholders[1].text_frame.add_paragraph()
    r1 = para.add_run(); r1.text = "Misfolding causes causes "; r1.font.bold = True
    r2 = para.add_run(); r2.text = "very serious disease in 2% of cases"
    # table
    tb = p.slides.add_slide(p.slide_layouts[5]); tb.shapes.title.text = "Data table"
    t = tb.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(6), Inches(1.5)).table
    t.cell(0, 0).text = "Sample  name"; t.cell(0, 1).text = "Yield yield"; t.cell(1, 0).text = "A-1"; t.cell(1, 1).text = "45%"
    # group with textbox
    g = tb.shapes.add_group_shape()
    tbx = g.shapes.add_textbox(Inches(1), Inches(4), Inches(4), Inches(1)); tbx.text_frame.text = "Note: results are are preliminary"
    f = os.path.join(TMP, "science.pptx"); p.save(f); return f

def deck_spanish():
    p = Presentation()
    add_slide(p, "Resumen  del  proyecto", ["El equipo trabajó trabajó en 3 módulos", "muy muy importante para la salud"])
    add_slide(p, "Conclusión", ["Los resultados son  buenos", "Se necesita más más tiempo"])
    f = os.path.join(TMP, "spanish.pptx"); p.save(f); return f

def deck_urdu_picture():
    p = Presentation()
    add_slide(p, "تعارف", ["یہ ایک ٹیسٹ جملہ ہے", "دوسرا نقطہ یہاں ہے"])
    s = p.slides.add_slide(p.slide_layouts[6]); png = os.path.join(TMP, "x.png"); _png(png)
    s.shapes.add_picture(png, Inches(1), Inches(1))
    f = os.path.join(TMP, "urdu.pptx"); p.save(f); return f

def deck_no_text():
    p = Presentation(); s = p.slides.add_slide(p.slide_layouts[6]); png = os.path.join(TMP, "y.png"); _png(png)
    s.shapes.add_picture(png, Inches(1), Inches(1)); p.slides.add_slide(p.slide_layouts[6])
    f = os.path.join(TMP, "notext.pptx"); p.save(f); return f


# ---------- mock LLMs: read the REAL prompt payload, return JSON ----------
def _items(prompt):
    m = re.search(r"<<<SLIDES_JSON\n(.*)\nSLIDES_JSON>>>", prompt, re.S)
    return [i for g in json.loads(m.group(1)) for i in g["items"]]

def _clean(t):
    t = re.sub(r"\b(\w+)\s+\1\b", r"\1", t, flags=re.I)
    t = re.sub(r"\b(very|really|basically|muy)\s+", "", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t); t = re.sub(r"\s+([.,;:!?])", r"\1", t).strip()
    return (t[:1].upper() + t[1:]) if t else t

def llm_good(prompt):
    ed = [{"id": i["id"], "text": _clean(i["text"])} for i in _items(prompt) if _clean(i["text"]) != i["text"]]
    return "```json\n" + json.dumps({"edits": ed}, ensure_ascii=False) + "\n```"

def llm_adversarial(prompt):          # invents numbers, translates, bloats, unknown id, drops a number
    its = _items(prompt); ed = []
    for k, i in enumerate(its):
        if k % 4 == 0: ed.append({"id": i["id"], "text": i["text"] + " by 99%"})
        elif k % 4 == 1: ed.append({"id": i["id"], "text": "日本語のテキスト"})
        elif k % 4 == 2: ed.append({"id": i["id"], "text": i["text"] * 3})
    ed.append({"id": "s99:1:tf:p0", "text": "ghost"})
    return json.dumps({"edits": ed})

_calls = {"n": 0}
def llm_flaky(prompt):                # garbage first, then ok
    _calls["n"] += 1
    return "Sorry, here you go!!" if _calls["n"] % 2 == 1 else llm_good(prompt)

def llm_down(prompt): raise RuntimeError("429 quota exceeded")


# ---------- tests ----------
fails = []
def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {info}" if info and not cond else ""))
    if not cond: fails.append(name)

decks = {"business": deck_business(), "science": deck_science(), "spanish": deck_spanish(),
         "urdu+picture": deck_urdu_picture(), "no-text": deck_no_text()}

print("== good LLM on 5 different decks ==")
for name, f in decks.items():
    dst = f.replace(".pptx", "_out.pptx")
    r = run_pipeline(f, dst, llm_good)
    sc = r["scores"]
    print(f"[{name}] slides={r['slides']} units={r['units']} applied={len(r['applied'])} rejected={len(r['rejected'])} "
          f"score {sc['before']['overall']} -> {sc['after']['overall']}")
    check(f"{name}: final file verified", r["verification"]["ok"], r["verification"])
    check(f"{name}: comparison matches applied", r["comparison"]["items_changed"] == len(r["applied"]))
    if name in ("business", "science", "spanish"):
        check(f"{name}: something rewritten", len(r["applied"]) > 0)
        check(f"{name}: score improved", sc["delta_overall"] > 0, sc["delta_overall"])
    if name == "urdu+picture":
        check("urdu: not corrupted/translated", len(r["applied"]) == 0 or all(x["reason"] != "language_changed" for x in r["rejected"]))
    if name == "no-text":
        check("no-text: zero AI calls, no crash", r["ai_calls"] == 0)

print("\n== formatting preserved (science multi-run bold) ==")
out = Presentation(decks["science"].replace(".pptx", "_out.pptx"))
par = [p for p in out.slides[0].placeholders[1].text_frame.paragraphs if p.text.startswith("Misfolding")][0]
print("  paragraph:", repr(par.text), "| runs:", [(r.text, r.font.bold) for r in par.runs])
check("bold run retained", any(r.font.bold and r.text.strip() for r in par.runs))
check("repeated word fixed in table/group too",
      "are are" not in " ".join(u.text for u in extract_units(out)) and "Yield yield" not in " ".join(u.text for u in extract_units(out)))

print("\n== guards: adversarial LLM ==")
dst = decks["business"].replace(".pptx", "_adv.pptx")
r = run_pipeline(decks["business"], dst, llm_adversarial)
reasons = {x["reason"] for x in r["rejected"]}
print("  rejected reasons:", reasons, "| applied:", len(r["applied"]))
check("invented numbers rejected", "numbers_changed" in reasons)
check("translation rejected", "language_changed" in reasons)
check("bloat rejected", "too_long" in reasons)
check("unknown id rejected", "unknown_id" in reasons)
check("nothing bad applied, file verified", len(r["applied"]) == 0 and r["verification"]["ok"])

print("\n== resilience ==")
r = run_pipeline(decks["business"], decks["business"].replace(".pptx", "_flaky.pptx"), llm_flaky)
check("flaky LLM: retry recovers", len(r["applied"]) > 0 and r["verification"]["ok"], r["ai_failures"])
r = run_pipeline(decks["business"], decks["business"].replace(".pptx", "_down.pptx"), llm_down)
check("LLM down: output == original, no crash", len(r["applied"]) == 0 and r["verification"]["ok"] and r["ai_failures"])
check("parse_edits handles fenced/bare/garbage", parse_edits('```json\n{"edits":[{"id":"a","text":"b"}]}\n```') and parse_edits('[{"id":"a","text":"b"}]') and parse_edits("nope") is None)

print("\n== sample comparison + recommendations (business) ==")
r = run_pipeline(decks["business"], decks["business"].replace(".pptx", "_out2.pptx"), llm_good)
for ch in r["comparison"]["slides"][0]["changes"][:3]:
    print(f"  - [{ch['role']}] {ch['before']!r} -> {ch['after']!r}")
print("  before recs:", r["scores"]["before"]["recommendations"])
print("  after  recs:", r["scores"]["after"]["recommendations"])
print("\nRESULT:", "ALL PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)