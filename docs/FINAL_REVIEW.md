# SAFECheck 2.13.0 · Baigiamasis inžinerinis auditas

Data: 2026-10-06. Tai esamo 2.12.0 boto auditas ir sustiprinimas. Veikiančios reputacijos, SCAM, pranešimų, TRUSTED, TOP, administratorių bei grupių taisyklės išlaikytos. Sąsaja ir toliau naudoja vieną bendrą DB bei LT / EN / RU vertimus.

## Audito eiga ir pataisymai

Atskirai dirbo saugumo, duomenų ir Telegram UX / QA agentai. Operacijų įgyvendinimą papildomai peržiūrėjo nepriklausomas agentas. Pagal užsakymą naudotas `gpt-6.1-sol` su `ultra` reasoning. Pataisymus ir bendrą patikrą integravo pagrindinis inžinierius; agentų išvada nepakeitė testų.

| Rasta problema | Galutinis elgesys | Regresijos patikra |
| --- | --- | --- |
| Administratorius galėjo praeiti teisių patikrą prieš savininkui atšaukiant prieigą ir vėliau užbaigti laukusį veiksmą. | Jautrūs įrašymo veiksmai naudoja bendrą tapatybės / prieigos užraktą ir dar kartą tikrina naujausias teises transakcijoje. | Septyni veiksmai, atnaujinta tos pačios sesijos rolė ir tikras PostgreSQL konkuruojančių transakcijų testas. |
| Rankinis username SCAM įrašo papildymas galėjo pasirinkti ID, prieštaraujantį jau stebėtai to username paskyrai. | Konfliktas atmetamas prieš pakeitimą, auditą ir blokavimo užduotį. Sutampantis ID leidžiamas aiškiai patvirtinus; REP ir pranešimų istorija neperkeliama. | SQLite ir PostgreSQL scenarijai su nauju / esamu ID, istorijos išsaugojimas. |
| Kai kurie Unicode skaitmenys praeidavo callback patikrą, tačiau netiko puslapio konvertavimui; netaisyklingas callback galėjo panaikinti pranešimo juodraštį. | Puslapių numeriai priima tik ASCII skaitmenis. Juodraštis išvalomas tik po sėkmingos callback validacijos. | Netaisyklingų callback, vedlių ir atšaukimo scenarijai. |
| Žinomas ID su neišspręstu istoriniu to paties username SCAM įrašu galėjo matyti pernelyg aiškų „įrašų nerasta“ rezultatą. | Atskiras geltonas perspėjimas visomis trimis kalbomis: username sutampa, tačiau SCAM ir konkretaus ID ryšys nepatvirtintas. Automatinio kaltinimo ar blokavimo nėra. | Trys kalbos, username pakeitimas ir tikro ID įrašo pirmenybė. |
| Versijos grąžinimas neišsaugodavo pasirinkimo kitam paleidimui. | Patikrinus schemos suderinamumą, image pasirinkimas atominiu būdu išsaugomas privačiame Compose env faile prieš stabdant botą. | Grąžinimas ir vėlesnis `start`, symlink / dubliuotos reikšmės atmetimas, sausas paleidimas. |
| Atkūrimas galėjo pakeisti DB prieš patikrindamas Redis prieinamumą ir atkūrimo teises. | PostgreSQL / Redis būklė ir atskiro Redis priežiūros vartotojo `PING` patikrinami prieš stabdymą bei DB pakeitimą. | Sugedęs Redis ir netinkamos priežiūros teisės nepradeda destruktyvaus atkūrimo. |

Papildomai apriboti konfigūracijos ID, pridėtas polling / worker progreso indikatorius ir agreguota operacijų būsena be vartotojų tapatybių ar pranešimų turinio.

## Architektūra ir duomenys

Telegram handleriai, klaviatūros, lokalizacija, pateikimas, servisai ir repository lieka atskirti. Nauji `app.health` ir `app.operations_status` papildo veikimo priežiūrą. `deploy/` yra operatoriaus CLI ir systemd šablonai, o ne papildoma boto administravimo ar reputacijos sistema.

Alembic head lieka `0009`; šiam leidimui migracijos ir duomenų perkūrimo nereikia. Vartotojas identifikuojamas Telegram ID, username lieka keičiamas metaduomuo. Istoriniai sprendimai tarp paskyrų neperkeliami vien dėl username sutapimo. Testai naudoja atskiras SQLite bazes, izoliuotas PostgreSQL schemas ir atskirus Redis raktus / testines paslaugas.

## Įdiegimo ir automatizacijos ribos

Paruošti: Docker Compose su slaptų failų įkėlimu, sveikatos patikros, saugus atnaujinimas / grąžinimas, kasdienės kopijos, kiekvienos kopijos pilnas bandomasis atkūrimas, kopijų saugojimo terminas, rclone adapteris ir pasirenkami operatoriaus perspėjimai. Komandos bei failų teisės: [OPERATIONS.md](OPERATIONS.md).

Nuolatinis VPS ir jo systemd laikmačiai šioje aplinkoje neįdiegti. Realus offsite gavėjas, kredencialai, retention politika ir Telegram perspėjimų gavėjas nekonfigūruoti. Rclone bei perspėjimų transporto testai yra imituoti. Laikino darbo serverio veikimas nėra nuolatinio hostingo garantija; serverio dingimą turi stebėti išorinė sistema.

Telegram Bot API nepateikia garantuotai visų grupės narių sąrašo ir nepatvirtina bet kokio username / ID ryšio. Username atsinaujina gavus patikimą Telegram vartotojo įvykį. Blokavimui būtinas žinomas ID, patvirtinta prižiūrima grupė ir boto teisės; garantijų apie neegzistuojančias teises nėra.

## Priėmimo įrodymai

Galutiniai komandų rezultatai ir laikino boto įdiegimo būsena pateikiami [RELEASE_2_13.md](../RELEASE_2_13.md). Po paskutinių pataisymų **569 testai praėjo per 111,52 s**, be praleistų testų; į šį skaičių įtraukti **44 operacijų testai**. Atskirai **89 testai praėjo su Docker image įdiegta aplikacija**, nemontuojant aplikacijos šaltinio. Ruff, formatas, mypy, priklausomybės ir Docker build patikrinti. Tikras izoliuotas Compose kopijų / DB atkūrimo scenarijus taip pat praėjo.

Laikinas botas atnaujintas į 2.13.0 tik patikrinus šviežios privačios kopijos pilną atkūrimą atskirame konteineryje. Telegram polling, worker, priklausomybės ir Docker health patikros praėjo. Ankstesnis konteineris paliktas sustabdytas grąžinimui; schemos migracija šiam leidimui nebuvo reikalinga.

Automatiniai testai tikrina verslo logiką ir Telegram transportą; tai nepakeičia vizualinės patikros vartotojo telefone, realaus VPS perkrovimo bei atkūrimo iš išorinės saugyklos repeticijos. Auditas sumažina žinomą riziką, tačiau nėra pažadas, kad ateityje nebus klaidų.
