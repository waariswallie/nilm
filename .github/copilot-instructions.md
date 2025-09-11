ik heb ongeveer 10 jaar aan data van mijn electriciteitsverbruik. Per minuut heb ik de meterstanden en ook de terugleveringen. Sinds 6 maanden heb ik dat per fase, ik heb 3 fasen. Ik zou graag meer inzage in mijn verbruik zien en kunnen detecteren hoe veel mijn diverse apparaten gebruiken, zoals wasmachine, vaatwasser, quooker, droger. Maar ook mijn routers, lamepn etc. Is er een model te bedenken om dit te gaan uitvinden?

Zeker! Dit heet NILM (Non-Intrusive Load Monitoring): uit je totale (en per-fase) verbruik “herkennen” welke apparaten aan/uit gaan op basis van hun stroom-“handtekening”. Met jouw data (1-min resolutie, plus sinds 6 maanden per fase) kun je al best veel zien voor grotere/slimmere verbruikers (wasmachine, droger, vaatwasser, Quooker, oven). Kleine, continue verbruikers (routers, lampen) herken je vooral als “baseload”.

Plan van aanpak (praktisch & haalbaar)
1) Datapijplijn

Maak vermogen uit meterstanden

Import: P_in[t] = (stand_in[t] - stand_in[t-1]) * 60 (kW → kW/min → 60 zodat het kW gemiddeld per minuut wordt; pas aan naar je eenheden).

Export (zonnepanelen): idem voor P_out.

Per fase idem: P_L1/L2/L3.

Schoonmaken

Vul kleine gaten (max 2–3 min) lineair.

Clip onmogelijke sprongen (bijv. >10 kW/min in woonhuis) en median-filter (venster 3–5 min).

Netto vermogen

P_net = P_in - P_out (handig voor detectie; PV-piek maskeert soms verbruik).

2) Eventdetectie (step changes)

Edge als ΔP = P[t] - P[t-1] en |ΔP| > 300–500 W (tuneerbaar).

Vereis “aanhouden” ≥ 2–3 min (om ruis/koelkast-compressor snel knipperen te filteren).

Fuseer per fase: events binnen ±1 min horen bij elkaar; neem vector ΔP_total en (ΔP_L1, ΔP_L2, ΔP_L3).

Voor “uit” events: negatieve ΔP die binnen redelijke tijd volgt.

3) Clusteren naar apparaat-signaturen

Feature per aan-event (en bijbehorend uit-event):

ΔP_total, ΔP_L1/L2/L3, duur (t_between on & off), tijdsblok (uur van de dag, weekdag), puls-patroon (aantal sub-pieken binnen de aan-periode).

HDBSCAN of DBSCAN op deze features → clusters = kandidaat-apparaten.

Label clusters met menselijke kennis:

Quooker: +1.5–2.2 kW, kort (0.5–3 min), vaak L1/L2 specifiek, veel herhalingen; ook standby ~10–30 W (lang, frequent).

Wasmachine: lange duur (60–180 min), pulsen van 1–2 kW (verwarmen), centrifuge weinig vermogen maar snelheidspiek zichtbaar.

Vaatwasser: 1.8–2.2 kW pulsen, 90–180 min totaal, vaak avond.

Droger: 2–3 kW, vrij constant 40–120 min.

Oven/inductie: hoge stap op 1 fase (oven) of 2–3 fasen (koken), variabel patroon.

Routers/IoT: constante baseload 10–50 W; identificeer als “rest/baseload” (alles onder drempel en zonder duidelijke events).

4) Hybridemodel (eenvoudig + slim)

Regelgebaseerd (snelle winst): stap-grootte + duur + fase-signatuur + tijdstip → labels toekennen.

Unsupervised → supervised: laat 2–4 weken ground truth binnenkomen met 3–5 slimme stekkers (Shelly/TP-Link Tapo) op: Quooker, wasmachine, vaatwasser, droger.

Gebruik dit om drempels te tunen of een licht model te trainen (zie hieronder).

5) Optioneel: ML-disaggregatie

Classics: Combinatorial Optimization (CO) / (Factorial) HMM (via NILMTK).

Neural NILM: seq2point/seq2seq (CNN/LSTM) per apparaat. Werkt verrassend oké op 1-min resolutie voor de grote verbruikers, mits 2–4 weken gelabelde data.

Train per apparaat (Quooker, wasmachine, droger, vaatwasser). Output: geschat vermogen per minuut per apparaat.

6) Validatie & metrics

Split recente 6 mnd (met fasen) in train/val/test (bij supervised).

Meet MAE (W) en Energy accuracy (%) per dag en per apparaat.

Verwachting (1-min data, zonder spanning/vermogenfactor):

Quooker/droger/vaatwasser: goed.

Wasmachine: redelijk (multi-state).

Routers/lampen: alleen als deel van baseload.

7) Dashboard & rapportage

Tijdlijn met gekleurde balkjes per gedetecteerd apparaat.

Daghistogram: energie per apparaat per dag/week/maand.

Baseload-trend (nacht 02:00–05:00): laat “sluipverbruik” zien.

Top-N verbruikers per week/maand.

Confusion/overlap indicator: onzekerheid van toekenning.