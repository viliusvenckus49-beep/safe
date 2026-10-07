# SAFECheck 2.2.1

- Administratoriaus `/add_sc @vartotojas priežastis` ir `/add_sc 123456789 priežastis` veikia grupėse. Priežastis: 10–1500 simbolių. ID rašomas be kabučių.
- Žinomas vartotojo vardas susiejamas su Telegram ID; nežinomą vardą galima įrašyti į registrą, bet be ID automatinis blokavimas neatliekamas. Atsakyme pateikiamas perspėjimas.
- „Atšaukti“ ir „Uždaryti“ panaikina dabartinį botų langą ir išvalo vedlio būseną. `/cancel` uždaro prisimintą langą. Naujas meniu nepateikiamas automatiškai.
- Aiškiai iškvietus `/start`, `/ask`, `/top`, `/scammers` ar administravimo komandą atnaujinamas dabartinis langas, kai jis prieinamas.
- Įprastos žinutės nesukuria meniu. `/del_sc` ir privatūs administravimo vedliai lieka asmeniniame pokalbyje.
- Jei Telegram neleidžia ištrinti seno pranešimo, pašalinami jo mygtukai. Anksčiau neprisiminti seni pranešimai automatiškai nevalomi.
- Duomenų bazės schema nesikeičia (Alembic 0004).

## Patikra

170 automatinių testų praėjo su PostgreSQL ir Redis. Ruff, formatavimo patikra, mypy (25 moduliai), pip check ir Docker build praėjo. Nepriklausoma ekrano / klaviatūrų / pateikimo peržiūra: 16 testų praėjo. Paleidimo `--check` sėkmingas. Realių vartotojų bandomasis blokavimas neatliktas.
