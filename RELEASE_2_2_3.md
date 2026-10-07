# SAFECheck 2.2.3

Navigacijos langai siunčiami kaip nauji pranešimai pokalbio apačioje. Sėkmingai išsiuntus naują langą, jo ID išsaugomas vedlio būsenoje ir ankstesnis botų langas ištrinamas. Jei siuntimas nepavyksta, ankstesnis langas išlieka. Jei Telegram neleidžia ištrinti seno pranešimo, jo mygtukai pašalinami.

„Atšaukti“ ir `/cancel` tik uždaro langą bei išvalo vedlį. Atnaujintas elgesys taikomas navigacijai, įvedimo žingsniams, moderavimo patvirtinimui ir veiksmų klaidoms. Įrodymų failai lieka atskiri pranešimai.

Per atnaujinimą izoliuotas FSM kontekstas apsaugo nuo vartotojų būsenų sumaišymo. Įvykiai tam pačiam vartotojui izoliuojami esamu Redis/SimpleEventIsolation mechanizmu. Duomenų bazės schema nesikeičia.

Patikra: 172 testai praėjo su PostgreSQL ir Redis (32,76 s). Ruff, formatavimas, mypy (25 moduliai), pip check ir Docker build sėkmingi. Nepriklausoma ekrano / pateikimo / klaviatūrų patikra: 15 testų praėjo. Tikras Telegram telefono slinkimas dar nepatikrintas.
