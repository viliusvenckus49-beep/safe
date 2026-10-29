# Prekeivių meniu patikslinimas

2026-10-29 naudotojo nurodymu pašalinti „＋ REGISTRUOTIS“ ir „ⓘ INFORMACIJA“ iš viešo prekeivių meniu. Pagrindinio boto meniu informacijos mygtukas nekeičiamas.

Viešame prekeivių meniu lieka miestai, paieška, „Mano įrašas“ turintiems įrašą ir grįžimas į pagrindinį meniu. Pašalintas ir pakartotinės registracijos mygtukas atmestų ar pašalintų įrašų ekrane.

Naujus prekeivius prideda esamas administratorius per „Prekeivių valdymas → Pridėti prekeivį“. Tapatybė toliau siejama su jau žinomu Telegram ID, o viešinimas vyksta per esamą patvirtinimą. Savarankiškas CREATE juodraščio pradėjimas, keitimas ir pateikimas blokuojami serveryje. Seni registracijos callback'ai pateikia lokalizuotą paaiškinimą; pašalintas informacijos callback'as laikomas pasenusiu.

Esamų patvirtintų įrašų savininkai išsaugo moderuojamus pakeitimo ir pašalinimo prašymus. Seni juodraščiai ir paraiškos netrinami; galima peržiūra ir atšaukimas, administratoriai išsaugo jų valdymą. Nustatymo „registrations“ DB laukas nekeičiamas, jo mygtukas dabar vadinasi „Profilio pakeitimų prašymai“ ir nesuteikia savarankiškos registracijos teisės.

Schema išlieka 0024; migracijų, REP, SCAM, TRUSTED, AUTO ADS ar GROUP HELP logikos pakeitimų nėra. Atnaujinti LT/EN/RU tekstai, Telegram navigacijos ir servisų teisių testai. Galutiniai pilnos CI ir diegimo rezultatai saugomi šio pakeitimo PR aprašyme.
