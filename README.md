# NILM

Non-Intrusive Load Monitoring (NILM) toolkit / playground.

Doel: energieverbruik van individuele apparaten afleiden uit een totale (slimme meter) vermogens- of energie tijdreeks.

## Eerste scope
- Datastructuur voor meter data (timestamp, consumption_power_w)
- Preprocessing: resampling (1s / 10s / 1m), smoothing, gap filling
- Feature extractie: delta P, run-lengths, rolling std/mean, on/off events
- Baseline disaggregatie: eenvoudige drempel + heuristiek
- Evaluatie: MAE per apparaat, on/off detectie (precision/recall/F1)

## Snelle start
```
python -m venv .venv
. .venv/Scripts/Activate.ps1  # Windows PowerShell
pip install -r requirements.txt
pytest -q
```

## Structuur (gepland)
```
src/nilm/
  __init__.py
  io/
    loader.py          # CSV / (toekomst) DSMR API
  preprocessing.py     # resample, clean, fill
  features.py          # feature engineering
  disaggregation/
    baseline.py
    heuristics.py
  evaluation.py        # metrics
notebooks/
  01_explore.ipynb
  02_baseline_disaggregation.ipynb
tests/
  test_baseline.py
```

## Roadmap (kort)
- [ ] Dataschema + loader
- [ ] Preprocessing pipeline
- [ ] Baseline model (threshold + minimale aan/uit duur)
- [ ] Evaluatie metrics
- [ ] Notebook demo
- [ ] Uitbreiding: clustering / HMM / ML model

## Licentie
MIT (placeholder)
