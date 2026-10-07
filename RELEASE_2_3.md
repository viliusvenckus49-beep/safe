# SAFECheck 2.3.0 — LT / EN / RU

## Įgyvendinta

Vienas botas ir viena bendra duomenų bazė su kiekvieno vartotojo pasirinkta sąsajos kalba: lietuvių, anglų arba rusų.

- Pirmas `/start`, įskaitant senus vartotojus be pasirinkimo, rodo kalbos pasirinkimą.
- Pasirinkimas išsaugomas pagal Telegram ID; pakartotinis `/start` atidaro meniu.
- Meniu „Kalba“ ir `/language` leidžia pakeisti kalbą; patvirtinimas rodomas nauja kalba, nebaigtas vedlys uždaromas.
- Lokalizuoti vieši ir administravimo ekranai, reputacija, pranešimų vedliai, statusai, statistika, auditas, mygtukai, klaidos, perspėjimai, instrukcijos ir komandų aprašymai.
- Grupėje atsakoma veiksmo autoriaus pasirinkta kalba. Atkūrimo pranešimai siunčiami gavėjo kalba.
- HTML sauga, callback'ų schemos, prieigos kontrolė, REP moderavimas, SCAM įrašai ir verslo taisyklės išlaikytos.

## Architektūra

`app/i18n.py`: viena `t()` funkcija, atskiro atnaujinimo kalbos kontekstas, LT fallback, saugus atsakymas trūkstamam raktui ar netinkamam šablonui. `app/locales/core.py` ir `presentation.py`: po 193 vienodus vertimų raktus kiekvienai kalbai, vardiniai placeholderiai. `app/bot/commands.py`: lokalizuoti komandų meniu. `app/bot/errors.py`: kalba iš naujo nustatoma ir po middleware klaidos. Pateikimo ir klaviatūrų moduliai naudoja bendrą lokalizacijos ribą.

Vartotojų vardai, priežastys, įrodymai ir ankstesni moderavimo įrašai lieka originalia kalba. Jie nėra automatiškai verčiami ar dubliuojami. `legacy/` yra neaktyvus V1 archyvas; operatoriaus paleidimo tekstai nėra Telegram sąsaja.

## Duomenų bazė ir diegimas

Alembic `0005`: vienas nullable `users.language` laukas su LT/EN/RU reikšmių patikra. Esami vartotojai pradžioje neturi pasirinkimo. PostgreSQL veikiantis testavimo egzempliorius ir SQLite migracijų bandymai išsaugojo senus įrašus.

Prieš migraciją padaryta privati DB kopija už repository ribų. PostgreSQL senų įrašų skaičių ir turinio kontrolinių sumų palyginimas patvirtino tikslų visų 21 duomenų lentelės išsaugojimą. `alembic check` nerado papildomų operacijų; paleidimo `--check` sėkmingas. Paleistas `safecheck:2.3.0`, patvirtintas `polling_started`.

```sh
alembic upgrade head
python -m app.main --check
python -m app.main
```

Naujų paslapčių ar konfigūracijos parametrų nereikia.

## Patikra

- Esamas bazinis rinkinys: **172 testai praėjo** (27,36 s).
- Galutinis visas rinkinys su tikru PostgreSQL ir Redis: **235 testai praėjo**, be praleidimų (41,84 s).
- Kalbos pasirinkimas visomis trimis kalbomis, seni vartotojai, visi keitimo deriniai, išsaugojimas ir tapatybės pokyčiai.
- Lokalizuoti profiliai, REP užklausos ir jų bendri duomenys, TOP, SCAM tikrinimas, registras, grupių add/del administravimas, reportų vedlis, klaidos ir callback'ai.
- Katalogų raktų/placeholderių sutapimas, HTML escaping, fallback, concurrent kalbos konteksto izoliacija.
- Gavėjų kalba atkūrimo worker'yje, ankstyvas cooldown/anoniminio siuntėjo atmetimas ir bendras klaidų atsakymas visomis kalbomis.
- SQLite migracijų upgrade/check/downgrade ir esamų vartotojų, REP, SCAM, reportų, vardų istorijos, audito turinio išsaugojimas.
- Ruff check, Ruff format check, mypy (31 modulis), pip check ir Docker build sėkmingi.
- Aktyvių Telegram išvesties vietų AST paieškos ir vertimų raktų testai praėjo. Aktyvioje sąsajoje neaptikta lokalizacijos ribą apeinančių tekstų.
- Repository kredencialų formato paieška: atitikmenų nėra; TODO/FIXME app/tests/migrations: nėra.
- Nepriklausoma saugumo/lokalizacijos peržiūra patvirtino įgyvendinimą.

Tikro telefono LT/EN/RU ekrano vaizdo patikra palikta vartotojo priėmimo bandymui. Šios darbo aplinkos botų paleidimas nėra nuolatinio hostingo SLA.
