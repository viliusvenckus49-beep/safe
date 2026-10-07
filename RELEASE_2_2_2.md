# SAFECheck 2.2.2

- TOP 10: dešimt sunumeruotų eilučių, HTML `<pre>`, lygiuoti vardų ir REP stulpeliai. Vardai lentelėje apriboti iki 18 pozicijų, mygtukuose lieka pilni vardai. Emoji pašalinami tik lentelės varduose dėl nuo įrenginio priklausančio pločio. Prieiga prie profilių išlieka.
- Pateikus pranešimą rodoma tik „📨 Pranešimas pateiktas“. Numeriai išlieka duomenų bazėje ir moderavimo languose.
- Administratoriaus `/del_sc @vartotojas priežastis` ir `/del_sc 123456789 priežastis` veikia grupėse. Priežastis: 10–1500 simbolių. Administratoriaus tapatybė tikrinama serveryje. SCAM istorija išsaugoma; pašalinimas iš registro neatšaukia Telegram banų automatiškai.
- Duomenų bazės migracijų nereikia.

Patikra: 174 testai praėjo su PostgreSQL ir Redis; Ruff, formatavimo patikra, mypy (25 moduliai), pip check ir Docker build sėkmingi. Paleidimo `--check` sėkmingas. Telegram telefono vaizdas dar nepatikrintas.
