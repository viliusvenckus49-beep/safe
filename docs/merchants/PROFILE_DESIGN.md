# Prekeivio kortelė ir TRUSTED perspėjimai

Aprašymai viešoje prekeivio kortelėje, savininko / administratoriaus peržiūroje ir paraiškų peržiūrose automatiškai įterpiami į `<blockquote><i>…</i></blockquote>`. Prieš formatavimą tekstas escapinamas; jo eilutės, pastraipos ir visas turinys išsaugomi. Saugojimo ir esamų įvesties ilgio ribų pakeitimų nėra.

Vieša kortelė turi kompaktiškus pavadinimo, Telegram tapatybės ir miesto laukus, „Apie prekeivį“, reputacijos bei saugumo skiltis. Reputacijos blokas taip pat italic ir blockquote, skaičiai išlieka bold ir imami iš esamos bendros REP sistemos. Katalogo patvirtinimo žyma atskirta nuo tikros TRUSTED žymos.

Bendra rodymo taisyklė `trusted_without_scam` naudoja jau esamo serviso apskaičiuotą TRUSTED reikšmę. `/ask` ir prekeivio kortelėje bendras patikimumo perspėjimas nerodomas tik turint TRUSTED ir nesant aktyvaus tiesioginio ar neišspręsto SCAM įrašo. Kitais atvejais perspėjimas išlieka. TRUSTED / SCAM suteikimo, galiojimo, prioriteto ir reputacijos skaičiavimo logika nekeičiama.

Naujos antraštės lokalizuotos LT/EN/RU. Profilių inline mygtukai, callback'ai ir vieno skydelio navigacija išsaugoti. Anksčiau pašalinti viešos registracijos, informacijos ir paieškos mygtukai negrąžinami. DB schema išlieka 0024; migracijų nėra.

Testai tikrina pilną HTML turinį ir pastraipas, literalines naudotojo HTML žymas, emoji bei Telegram dydžio ribą, perspėjimų taisyklę, tikrą `/ask` su izoliuotais TRUSTED / SCAM duomenimis, reputacijos skaičius ir esamą vieno pranešimo navigaciją. Galutiniai CI ir diegimo rezultatai pateikiami pakeitimo PR aprašyme.
