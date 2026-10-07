# SAFECheck 2.4.0 — uždaras grupių valdymas

Tik GROUP_OWNER_ID nurodytas SAFECheck savininkas gali parengti ir patvirtinti grupes, peržiūrėti jų valdymą, eksportuoti narius ir patvirtinti atkūrimo siuntimą. Savininkas turi būti ADMIN_IDS sąraše. Jei administratorius vienas, jis laikomas savininku; kelių administratorių atveju be aiškaus savininko grupių valdymas užblokuotas. Kitų administratorių reputacijos ir reportų teisės išlieka.

Nauja grupė, pridėjus/suteikus botui teises arba savininkui parašius /start, laukia patvirtinimo. Per /groups privačiai arba grupėje savininkas paspaudžia „Patvirtinti grupę“. Tikrinamos dabartinės Telegram grupės savininko/administratoriaus ir boto administratoriaus su blokavimo teise sąlygos. Neautorizuoti bei kitų grupių callback'ai negali aktyvuoti grupės.

Iki patvirtinimo nekaupiami grupės narių įrašai, nekuriami ir nevykdomi banai, neprijungiamos prenumeratos ir nesiunčiami atkūrimo pranešimai. Po patvirtinimo atsarginė kopija apima tik botui matytus narius. Nėra viešų paraiškų ir automatinio žmonių pridėjimo į grupę. Atkūrimas išlieka tik su gavėjo sutikimu ir savininko peržiūra/patvirtinimu. Jau patvirtintą grupę galima atkurti ir dingus senai grupei.

LT/EN/RU tekstai ir auditas papildyti. Grupės ekranuose nėra neveikiančių privataus administravimo mygtukų; privačiai valdymas per /groups. Grupių parengimas/patvirtinimas, narių kopijų eksportas ir atkūrimo siuntimas audituojami.

Alembic0006 prideda approved. Senos grupės išsaugomos patvirtintos; naujos pagal nutylėjimą nepatvirtintos. PostgreSQL privataus backup ir kontrolinių sumų patikra patvirtino visų senų įrašų išsaugojimą 21 duomenų lentelėje. SQLite upgrade/check/downgrade testas išsaugojo grupes, narius, prenumeratas, kampanijas ir siuntimo eilę.

Patikra: 252 testai praėjo su tikru PostgreSQL ir Redis (44,12s), be praleidimų; Ruff check/format, mypy32 moduliai, pipcheck, Dockerbuild, alembiccheck ir startup--check sėkmingi. Nepriklausoma saugumo peržiūra nerado autorizacijos blokatorių; atskirai7 teisių/owner testai ir10configtestų praėjo. Įdiegta safecheck:2.4.0. Nauja realios grupės aktyvacija bei realus vartotojo banas bandymui nebuvo atlikti.

```sh
alembic upgrade head
python -m app.main --check
python -m app.main
```

Esamoje aplinkoje savininko ID sukonfigūruotas pagal savininko pateiktą Telegram ID; papildomų duomenų iš vartotojo nereikėjo. Naujam hostingui nustatykite GROUP_OWNER_ID paslapčių/konfigūracijos aplinkoje. Tokenai ir DB kopijos nėra source archyve.
