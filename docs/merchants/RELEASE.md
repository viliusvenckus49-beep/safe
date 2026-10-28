# REDSAFE prekeivių katalogas — įgyvendinimo ir diegimo ataskaita

## Būsena ir ribos

Visos A–E fazės įgyvendintos vystymo šakoje `feature/redsafe-merchants-20261028`. Pagrindas — paskutinė patvirtinta boto versija `6d7ed89f17b966acac0ea409bf2a260e0df2bdae`. Naujas botas nekurtas, VPS nekeistas, produkcinė migracija nevykdyta. Diegti galima tik gavus atskirą leidimą. Galutinį patikrintą commit ir CI rezultatą žr. šakos PR patikrose.

## 1. Integracijos taškai ir navigacija

Pagrindinio meniu tekstas ir seni callback'ai išsaugoti. Asmeniniame meniu pridėtas `🏪 PREKEIVIAI`, esamoje administravimo panelėje — `🏪 PREKEIVIŲ VALDYMAS`. Grupėse privatus katalogo callback'as pateikia lokalizuotą paaiškinimą; katalogo paieška ir registracija vyksta asmeniniame pokalbyje.

```
Pagrindinis meniu → Prekeiviai
  → Miestai → pasirinktas miestas / visi → sąrašas → prekeivio profilis
      → patvirtinti atsiliepimai → visi / +REP / −REP
      → esama REDSAFE patikra / veiklos profilis → atgal į katalogą
  → Paieška → rezultatai → profilis → ankstesnis rezultatų puslapis
  → Registruotis → taisyklės → miestas → pavadinimas → aprašymas
      → privati peržiūra → pateikimas → administratoriaus sprendimas
  → Mano įrašas → būsena / juodraštis / pakeitimo prašymas / atšaukimas / pašalinimo prašymas

Administravimas → Prekeivių valdymas
  → laukiančios paraiškos → peržiūra → patvirtinti / atmesti su priežastimi
  → visi įrašai / paieška → peržiūra → keitimas / sustabdymas / atkūrimas / pašalinimas
  → pridėti žinomą prekeivį → privati paraiška → patvirtinimas
  → katalogo nustatymai → įjungimas, paraiškos, autorių privatumas, LT/EN/RU tekstai
  → katalogo arba konkretaus įrašo veiksmų istorija
```

Callback prefix `mt` atskirtas nuo esamų modulių. Paieškos tekstas nekeliauja callback_data. Vienos sesijos ekranai redaguojami; jei Telegram redagavimas neįmanomas, naudojama esama saugi skydelio pakeitimo logika. Po sėkmingos įvesties bandoma pašalinti tik to naudotojo įvesties pranešimą. Istoriniai moderavimo pranešimai neliečiami. Naujos komandos iškvietimas gali sukurti naują pradinį atsakymą.

## 2. Handler'iai ir servisai

- `register_merchant_handlers`: vienas katalogo callback handler'is, vienas tekstinės įvesties handler'is su atskira `MerchantFlow.input` būsena. Įvestis neperima `/ask`, `/rep`, kitų slash komandų ir `+rep`/`-rep`.
- `MerchantService`: katalogo politika, registracijos, juodraščiai, patvirtinimai, atmetimai, savininko ir administratoriaus prieiga, minkštas pašalinimas, nustatymai bei auditas.
- `MerchantRepository`: DB puslapiavimas, patvirtintų įrašų paieška, stabilus rūšiavimas ir atsiliepimų SQL filtrai.
- `SearchLimiter`: Redis `SET NX EX` riboja paiešką tam pačiam botui ir naudotojui kas 2 sekundes. Atminties variantas skirtas esamai be-Redis testavimo konfigūracijai. Bendras boto užklausų ribojimas išlieka.
- Vaizdai ir klaviatūros atskirti nuo verslo logikos. Dinaminiai laukai saugiai escapinami. Visas tekstas įtrauktas į esamą LT/EN/RU katalogą.

Paieška: dabartinis username su arba be `@`, visas ar dalinis viešas pavadinimas, Unicode normalizavimas ir nepriklausymas nuo raidžių dydžio. Dviejų simbolių DB indeksas leidžia ieškoti pavadinimo fragmentų be papildomos PostgreSQL plėtinio priklausomybės. Nerastas prekeivis automatiškai netikrinamas per /ask ir nevadinamas SCAM.

## 3. Duomenų struktūra ir migracija

Adityvi Alembic migracija: `0024_merchant_directory.py`, pirmtakas `0023`.

| Lentelė | Paskirtis ir pagrindinės apsaugos |
| --- | --- |
| `merchant_entries` | Vienas įrašas vienam esamam `users.id`; viešas pavadinimas, normalizuotas paieškos raktas, miestas, aprašymas, būsena, versija, datos, patvirtinęs administratorius ir priežastis. FK ir unikalumas pagal savininką. |
| `merchant_proposals` | Atskiras CREATE / EDIT / REMOVE juodraštis ar paraiška; versija, tikslinio įrašo bazinė versija, kūrėjas, datos, galiojimas, sprendimas ir priežastis. Unikalus užklausos raktas; tik viena DRAFT / PENDING paraiška įrašui. |
| `merchant_search_grams` | Viešai patvirtinto pavadinimo fragmentų indeksas; sudėtinis PK ir paieškos indeksas. |
| `merchant_settings` | Vienas katalogo politikos įrašas: veikimas, registracija, autorių viešinimas, lokalizuotos antraštės / aprašai ir versija. |

Naudojama bendra `AuditEvent` lentelė. Išsaugomi veikėjas, data, įrašo / paraiškos ID ir veiksmo rūšis. Slapti tokenai nesaugomi. Esamos REP, naudotojų, grupių ir SCAM lentelės netrinamos; REP sumos į katalogo lenteles nekopijuojamos. Tuščias katalogas po migracijos lieka tuščias, demonstracinių prekeivių nėra.

Užpildyto katalogo `downgrade` atsisako trinti duomenis. Produkinio diegimo apsauga papildyta tik tiksliu šios migracijos hash; pakeistos, nežinomos ar naujesnės migracijos vis tiek atmetamos. Duomenų išsaugojimo zondas po migracijos tikisi `0024`.

## 4. Registracijos ir administravimo politika

Registracija siejama su patikimai žinomu skaitiniu Telegram ID per esamą User. Registruotas pavadinimas nekeičia Telegram display name. Vienas savininkas turi vieną katalogo įrašą; pašalintą ar atmestą įrašą galima pateikti peržiūrėti iš naujo išsaugant istoriją.

Juodraščiai saugomi DB, galioja vieną valandą. Atskiros įvesties sesijos galioja 10 minučių. Ne administratorius gali pradėti iki trijų paraiškų per 24 valandas; aktyvus juodraštis pratęsiamas kaip tas pats objektas. Vienalaikiai sprendimai serializuojami esamu tapatybės / administravimo užraktu ir tikrinamos įrašo bei paraiškos versijos. Pakartotinis patvirtinimas nesukuria antro publikavimo ar audito įrašo.

Savininkas keičia tik savo paraiškos laukus. Vieši pakeitimai pasirodo tik patvirtinus. Sustabdyto įrašo turinio patvirtinimas jo automatiškai neįjungia. Pašalinimas minkštas; savininko prašymą patvirtina administratorius. Turinys ir istorija išlieka.

Administratoriai naudojasi esamu `Service.require_admin`, įskaitant esamą savininko ir atšauktų teisių politiką. Teisės pakartotinai tikrinamos serveryje ir prie transakcijos ribos, ne vien slepiant mygtukus. Neviešos paraiškos prieinamos tik savininkui ar esamam administratoriui. Katalogas nesuteikia papildomų boto savininko teisių.

## 5. Reputacija, SCAM ir privatumas

`Repository.rep_stats` ir katalogo SQL rūšiavimas naudoja bendrą esamo ledger užklausą: priimti istoriniai REP įvykiai, APPROVED reputacijos paraiškos ir administratoriaus korekcijos. Atskiros katalogo reputacijos nėra. TOP 10, SCAM, TRUSTED ir mokamų paslaugų taisyklės nekeičiamos.

Atsiliepimai rodomi tik iš patvirtintų paraiškų ir priimtų istorinių įvykių. PENDING ir REJECTED duomenys neviešinami. Administracinio reputacijos išvalymo metu esama sistema išsaugo istorinius įrašus ir kompensuoja statistiką; katalogas tai gerbia ir paaiškina galimą istorijos kiekio ir skaičių skirtumą. Viename puslapyje rodomas vienas pilnas atsiliepimas — tai telpa į nustatytą iki trijų ribą ir leidžia neprarasti ilgo 1500 simbolių komentaro su emoji dėl Telegram 4096 UTF-16 vienetų limito.

Atsiliepimų autoriai pagal nutylėjimą anoniminiai. Administratorius gali įjungti viešinimą tik pagal taikomą privatumo politiką. Nepatvirtinta tapatybė nežymima patvirtinta. Username pasikeitimas nepakeičia savininko ID ir neperduoda reputacijos kitam username savininkui.

Aktyvus SCAM įrašas išskiria prekeivį iš viešų sąrašų ir profilio užklausų pagal tą pačią esamą tapatybės taisyklę, įskaitant ribotus neišspręstus username įrašus. SCAM turi pirmenybę prieš TRUSTED. Katalogo patvirtinimas TRUSTED nesuteikia. Anksčiau patvirtintas ir dėl SCAM paslėptas įrašas gali vėl tapti matomas panaikinus SCAM, jei pats katalogo įrašas nėra sustabdytas ar pašalintas.

## 6. Atliktos patikros

- A: 3 miesto / formato / callback testai praėjo.
- B: 31 DB, reputacijos regresijos ir migracijų testas praėjo.
- C: 8 servisų testai praėjo, vienas PostgreSQL testas vietoje praleistas.
- D: 39 katalogo / kalbų / tikros aiogram navigacijos testai praėjo, vienas PostgreSQL testas vietoje praleistas.
- E: 74 tiksliniai testai praėjo, keturi PostgreSQL integraciniai testai vietoje praleisti.
- Įterpto testavimo paketo patikra: 57 praėjo, 14 PostgreSQL integracinių testų vietoje praleisti.
- Visų šaltinių Ruff lint ir format patikros praėjo; mypy patikrino 129 šaltinių failus be klaidų.

Tai persidengiantys fazių rinkiniai, jų skaičiai nesumuojami. Viso projekto regresijos ir PostgreSQL / Redis CI galutiniai rezultatai pateikiami PR ir galutinėje ataskaitoje. Neteigiama, kad katalogas jau išbandytas produkciniame Telegram pokalbyje.

## 7. Saugus įdiegimas gavus atskirą leidimą

1. Patikrinti PR galutinio commit pilnos CI sėkmę ir gyvo boto versiją. Neimti senesnės `main` šakos vietoje patikrintos vystymo šakos.
2. Patikrinti laisvą RAM / diską, kitų botų izoliaciją, naują PostgreSQL kopiją ir nepriklausomą atkūrimą. Produkcinės kopijos saugoti už VPS ribų pagal esamą operatoriaus tvarką.
3. Naudoti esamą `vps-deploy.yml` workflow su šios šakos tiksliu SHA ir patvirtintu pilnos push CI run ID. Diegimas daro šviežias kopijas, atkūrimo repeticiją, išsaugo esamus tomus ir atnaujina tik SAFECheck botą.
4. Leisti tik adityvią `0024` migraciją. Nestabdyti ir neperkurti bendrų PostgreSQL / Redis servisų, netrinti native užduočių ar istorijos ir neatkurti seno testinio boto.
5. Po diegimo patikrinti `0024`, runtime sveikatą, kito boto nepakitusią būseną, tuščio katalogo ekraną, administratoriaus teises ir naują teisėto realaus prekeivio paraišką. Nenaudoti fiktyvių produkcinių įrašų ar realių BAN/MUTE kaip testavimo šalutinių veiksmų.
6. Po migracijos aklai negrąžinti `0023` kodo ir neatkurti senos DB virš naujų įrašų. Avariniu atveju išjungti katalogą jo nustatymuose ir taikyti peržiūrėtą 0024 suderinamą pataisą. Istoriją turintis downgrade sąmoningai blokuojamas.

## 8. Apribojimai

Teisėtumo ir aprašymuose pateiktos informacijos peržiūra yra administratoriaus darbas; katalogo priėmimas nėra tapatybės, prekių ar sąžiningumo garantija. Privačių kontaktų rinkimo laukų nėra; taisyklės draudžia privačius kontaktus ir adresus. Miestų sąrašas centralizuotas; pajūrys yra regionas. Kitų naujų miestų įtraukimui reikės vertimų ir DB leistinų reikšmių migracijos.

Dideliam realių duomenų kiekiui našumą reikia matuoti atskirai; įgyvendinti DB indeksai, SQL filtravimas ir puslapiavimas, o ne visos istorijos parsisiuntimas į atmintį. Redis nebuvimo atminties ribotuvas nėra kelių procesų bendra saugykla; produkcijoje naudojamas esamas Redis.

## 9. Tikslus pakeistų failų sąrašas

```text
app/bot/callbacks.py
app/bot/handlers.py
app/bot/keyboards.py
app/bot/merchant_keyboards.py
app/bot/merchant_limits.py
app/bot/merchant_views.py
app/bot/merchants.py
app/bot/states.py
app/i18n.py
app/locales/merchant_workflows.py
app/locales/merchants.py
app/merchant_catalog.py
app/merchant_repository.py
app/merchant_services.py
app/models.py
app/repositories.py
deploy/production_probe.py
deploy/production_release.py
docs/merchants/IMPLEMENTATION.md
docs/merchants/RELEASE.md
migrations/versions/0024_merchant_directory.py
tests/test_auto_ads_live_operator.py
tests/test_auto_ads_migration.py
tests/test_merchants_data.py
tests/test_merchants_foundation.py
tests/test_merchants_migration.py
tests/test_merchants_service.py
tests/test_merchants_telegram.py
tests/test_production_release.py
tools/auto_ads_live_test.py
```
