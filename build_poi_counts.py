import json, re, math, openpyxl, os
reg = json.load(open("map_data.json", encoding="utf-8"))
pois = [p for p in json.load(open("poi_data.json", encoding="utf-8")) if p.get("c") != "supermarket"]
geo = {g["name"]: (g["lat"], g["lng"]) for g in json.load(open("opportunities_134a.json", encoding="utf-8"))}
wb = openpyxl.load_workbook("NSW_Shopping_Centre_Store_Counts.xlsx")
ws = wb["NSW Centres"]; hdr = [c.value for c in ws[1]]
iC, iS, iP, iO = (hdr.index("Centre"), hdr.index("Stores (directory total)"),
                  hdr.index("Pharmacies (in centre)"), hdr.index("Operator"))
counts = {}
for row in ws.iter_rows(min_row=2, values_only=True):
    if row[iC]:
        counts[row[iC]] = {"op": row[iO], "stores": row[iS], "ph": row[iP], "est": None}
for row in wb["134A Opportunities"].iter_rows(min_row=2, values_only=True):
    if row[1] in counts and isinstance(row[3], int):
        counts[row[1]]["est"] = row[3]

GEN = {"shopping", "shoppingtown", "centre", "center", "mall", "plaza", "city", "square",
       "village", "marketplace", "market", "the", "outlet", "town", "home", "homeco", "co",
       "stockland", "westfield", "grove", "court", "central", "sydney", "cbd", "of", "north", "south"}
def toks(s): return set(w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if w)
def _tok_sets(p):
    # the POI's own name plus every alias the centre-dedup recorded (original OSM + Places names)
    yield toks(p.get("n") or "")
    for a in p.get("aliases") or []:
        yield toks(a)
def is_exact(p, wt): return any(ts == wt for ts in _tok_sets(p))
def ptoks(p):
    s = set()
    for ts in _tok_sets(p):
        s |= ts
    return s
def D(a, b):
    R = 6371000; la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))
def coord(cn):
    if cn in geo: return geo[cn]
    dt = toks(cn) - GEN
    if not dt: return None
    pts = [(r["latitude"], r["longitude"]) for r in reg
           if r.get("latitude") and dt <= toks(r.get("premises_details") or "")]
    if not pts: return None
    pts.sort(); return pts[len(pts) // 2]
OPWORD = {"Westfield (Scentre)": "westfield", "Stockland": "stockland", "HomeCo (HMC)": "homeco"}
NEAR = 400
def key(p): return "%s|%.6f|%.6f" % (p.get("n") or "", p["lat"], p["lng"])

out = {}; rep = []
for cn, info in counts.items():
    if info["stores"] in (None, ""): continue
    cc = coord(cn); wt = toks(cn)
    ex = [p for p in pois if is_exact(p, wt)]
    pick = None
    if ex:
        # centres are de-duplicated upstream (enrich_centres), so tag the single nearest node only
        pick = min(ex, key=lambda p: D(cc, (p["lat"], p["lng"])) if cc else 0)
        out[key(pick)] = {**info, "cname": cn}
        rep.append((cn, pick.get("n"))); continue
    if cc:
        cand = [(p, D(cc, (p["lat"], p["lng"]))) for p in pois if (wt - GEN) & (ptoks(p) - GEN)]
        cand = [c for c in cand if c[1] < NEAR]
        if cand:
            pick = min(cand, key=lambda c: c[1])[0]
        else:
            ow = OPWORD.get(info["op"])
            if ow:
                cand = [(p, D(cc, (p["lat"], p["lng"]))) for p in pois if ow in ptoks(p)]
                cand = [c for c in cand if c[1] < NEAR]
                if cand:
                    pick = min(cand, key=lambda c: c[1])[0]
    if pick:
        out[key(pick)] = {**info, "cname": cn}; rep.append((cn, pick.get("n")))

# Supplement: extra centres scraped directly (not in the workbook), matched by POI name
if os.path.exists("extra_centre_counts.json"):
    extra = json.load(open("extra_centre_counts.json", encoding="utf-8"))
    for cname, info in extra.items():
        ct = toks(cname)
        cand = [p for p in pois if ct <= ptoks(p)]
        for p in cand:
            out[key(p)] = {"op": info.get("op", "Independent"), "stores": info["stores"],
                           "ph": info.get("ph"), "est": info.get("est"), "cname": cname}
        rep.append((cname, cand[0].get("n") if cand else "NO POI"))

json.dump(out, open("poi_counts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("wrote poi_counts.json with", len(out), "matched centres")
matched_names = {r[0] for r in rep}
un = [cn for cn in counts if counts[cn]["stores"] not in (None, "") and cn not in matched_names]
print("still unmatched (with counts):", un)
for cn, pn in sorted(rep):
    if cn in ("Erina Fair", "Westfield Hornsby", "Westfield Sydney (CBD)"):
        print("  recovered:", cn, "->", pn)
