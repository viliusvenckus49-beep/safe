# SAFECheck 2.5.0 — Administratorių valdymas ir instrukcija

Įdiegta savininko administratorių sąsaja `/admins`, pagrindinio administravimo mygtukas ir `/admin_help` su šešiais instrukcijos skyriais LT / EN / RU. Pridėjimas pagal skaitinį ID arba atsakant į žinutę privačiai reikalauja peržiūros ir patvirtinimo. Pašalinimas taip pat patvirtinamas. Teisės įsigalioja be perkrovimo. Savininko pašalinti negalima. Nauji administratoriai neturi grupių ar kitų administratorių valdymo teisių. Slash meniu lieka tik start / ask / rep / report.

Architektūra: AdminService, Repository, atskiri administratorių handleriai, callback’ai/FSM, klaviatūros, presentation ir visų trijų kalbų katalogas. Visi ankstesni administravimo handleriai ir paslaugos naudoja DB teisių patikrą. Konfigūracijos ADMIN_IDS lieka pradinis sąrašas, DB atšaukimas jį perrašo; GROUP_OWNER_ID lieka nekintamas savininkas.

Migracija 0007 prideda administrators ir administrator_changes. Numerinio ID pirminis raktas, ID apribojimas, užsienio raktas ir unikalus request_key saugo teises ir istoriją. Transakcijos serializuojamos, pakartojimų raktai sunaudojami net kai teisės nesikeičia, auditas rašomas keičiantis teisėms. Seni patvirtinimai negali atšaukti vėlesnio sprendimo.

Patikra: pilnas `python -m pytest -q -p no:cacheprovider` Docker testavimo tinkle su izoliuotu PostgreSQL ir Redis — 285 passed, 51.30 s, be praleidimų. `ruff check .`, `ruff format --check .` (87 failai), `mypy --no-incremental app` (37 moduliai), `pip check`, `git diff --check` praėjo. Docker atvaizdas pastatytas. Nepriklausoma saugumo peržiūra patvirtino pataisytą idempotentiškumą ir nerado likusių materialių problemų. Programos tokenų ir TODO/FIXME paieškos rezultatų nerado.

Diegimas: privati PostgreSQL kopija prieš migraciją, išsaugoti visų 21 senų lentelių turinio kontroliniai duomenys ir patikrintas jų sutapimas po migracijos. `alembic upgrade head`, `alembic check`, `python -m app.main --check` praėjo. Paleistas safecheck:2.5.0.

Paleidimas: `alembic upgrade head`, `python -m app.main --check`, `python -m app.main`. Docker komandų ir aplinkos nustatymų detalės README. Esamas savininkas sukonfigūruotas; naujo tokeno ar DB duomenų šiam atnaujinimui nereikia. Naujų administratorių skaitinius ID pateikia savininkas per botą.

Ribos: naujo realaus administratoriaus pridėjimas ir telefono UI priėmimo patikra palikta savininkui; testai nesuteikė teisių jokiam realiam žmogui. Botas nesiunčia naujam administratoriui nepageidaujamo pranešimo. Testavimo aplinkos nereikia laikyti nuolatine gamybine talpinimo garantija.
