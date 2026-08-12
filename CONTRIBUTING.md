# Contributing to HonestSpend

HonestSpend is **open-source freeware** for small business owners and anyone who wants a liquidity cockpit (not an investment app).

## Product north star

See [`docs/PRODUCT.md`](docs/PRODUCT.md). In short:

- Never go negative on **checking**
- Intentional **0% float** is allowed when fiscally sound
- **No dumb fees / unnecessary interest**
- **Opportunity cost** (yield vs cheap debt)
- **Fiscal soundness > credit score theater**
- Minimum user time — automation first

## Dev setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # or source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
python -m honestspend.cli serve
```

App: http://127.0.0.1:7420  
Data: `~/.HonestSpend/honestspend.db`

## Guidelines

1. **Trust over features** — Spendable numbers must be explainable and tested.
2. **Local-first** — no required cloud for core books.
3. **Recommendations include options + reasoning** — never silent “just do this.”
4. **Not tax/legal/investment advice** — keep disclaimers on tax/credit simulators.
5. Prefer pure engine functions + unit tests (see `tests/`).

## Versioning

- **1.0.0** = liquidity OS promise (see [`docs/RELEASE_1.0.0.md`](docs/RELEASE_1.0.0.md)).  
- Policy: [`docs/VERSIONING.md`](docs/VERSIONING.md). Dream work continues in **1.x**.

## PR checklist

- [ ] `pytest -q` passes (or `.\scripts\verify-grade-a.ps1`)  
- [ ] New fiscal logic has unit tests  
- [ ] UI copy matches PRODUCT.md priorities · Simple path stays jargon-free  
- [ ] No secrets committed (`.env`, API keys)

## License

MIT — see [LICENSE](LICENSE).
