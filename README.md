# Ventilation Coach

Lernende Lüftungsempfehlung für Home Assistant. Sie sagt dir, **wie lange** du lüften solltest, **was es bringt** und **wann du schließen sollst**. Dabei lernt sie für jede Zone, wie schnell dort die Luft ausgetauscht wird.

Entstanden aus der Automation „Lüften Live-Update“ und jetzt als eigene Integration umgesetzt: testbar, neustartfest und mit Unterstützung für mehrere Räume.

## Zonen statt Räume

Eine **Zone** ist ein zusammenhängendes Luftvolumen, zum Beispiel Wohnzimmer und offene Küche. Eine Zone kann mehrere Fenster sowie mehrere Feuchte- und Temperatursensoren enthalten:

- Feuchte und Temperatur werden gemittelt.
- **Ausreißerschutz:** Ein Feuchtesensor, der deutlich über dem trockensten liegt (Standard 10 Punkte, z. B. beim Kochen), wird ignoriert.
- **Fensterprofile:** τ wird zusätzlich je Fensterkombination gelernt, z. B. Querlüftung Küche + Wohnzimmer. Ab 3 Lernläufen wird das Profil genutzt, vorher der Zonenwert. Fenster, die nur kurz offen waren (weniger als die Hälfte der Zeit), zählen nicht zum Profil.

## Modell

Bei offenem Fenster nähert sich die Raumfeuchte der **Gleichgewichtsfeuchte** exponentiell an. Die Gleichgewichtsfeuchte ist der Außen-Taupunkt bei Raumtemperatur:

```
h(t) = eq + Abstand · e^(−t/τ)
```

- **Lüftdauer** = Maximum aus „bis der erwartete Abfall unter 0,3 Punkte/Min fällt“ (`τ·ln(Abstand/(0,3·τ))`) und „bis 20 % des Abstands geschlossen sind“ (`0,223·τ`). Dabei gilt mindestens 5 Min und höchstens 10/20/30/45 Min bei < 0/< 10/< 15/≥ 15 °C außen. Bei weniger als 8 Punkten Abstand gibt es nur 5 Min Frischluft.
- **Früher schließen** (ab 3 Min), wenn eine dieser Bedingungen eintritt:
  - Der Raum-Taupunkt liegt weniger als 1 K über dem Außen-Taupunkt.
  - Der Raum kühlt aus (≥ 3 K kälter oder < 16 °C).
  - Seit 10 Min kam keine Feuchtemeldung, obwohl das Modell ≥ 2 Punkte Abfall erwartet.
- **Lernen:** Nach dem Schließen wird bis zur ersten Feuchtemeldung gewartet (mind. 2, max. 15 Min). Daraus ergibt sich `τ_mess = −Dauer / ln(1 − Abfall/Abstand)`, begrenzt auf 0,5–2 × bisheriges τ. Der neue Wert fließt gleitend ein: in den ersten 3 Läufen gleich gewichtet, danach mit 30 %.
  - Ein Lauf ist nur gültig, wenn seit dem Öffnen eine neue Feuchtemeldung kam, das Fenster mindestens 8 Min offen war und der Abstand mindestens 8 Punkte betrug.

Die Logik steckt in reinem Python (`model.py`, `engine.py`) ohne Home-Assistant-Abhängigkeit. `zone.py` verbindet sie mit Home Assistant.

## Installation (HACS)

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → URL dieses Repos, Typ *Integration*.
2. *Ventilation Coach* installieren und Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Ventilation Coach*. Lege dabei pro Zone einen Eintrag an.

Bei der Einrichtung kannst du **Start-Zeitkonstante** und **bisherige Lernläufe** übernehmen. So geht nichts verloren, was eine bestehende Automation schon gelernt hat.

## Entitäten je Zone

| Entität | Bedeutung |
|---|---|
| `sensor.<zone>_status` | `idle` / `airing` / `close_now` / `settling`. Attribute: Grund, Hinweis, Fortschritt, Start-, aktuelle und erwartete Feuchte, Ende, offene Fenster |
| `sensor.<zone>_humidity` | Zonenfeuchte (gemittelt, ohne Ausreißer) |
| `sensor.<zone>_equilibrium_humidity` | Gleichgewichtsfeuchte jetzt |
| `sensor.<zone>_humidity_gap` | Abstand Raumfeuchte – Gleichgewicht |
| `sensor.<zone>_airing_duration` | Lüftdauer (laufend oder „wenn jetzt gelüftet würde“) |
| `sensor.<zone>_expected_humidity` | Erwartete Feuchte am Ende |
| `sensor.<zone>_airing_end` | Ende der laufenden Lüftung |
| `sensor.<zone>_last_airing` | Letztes Lüften mit Bilanz als Attribute |
| `sensor.<zone>_time_constant` | Gelerntes τ, Lernläufe, Fensterprofile |
| `binary_sensor.<zone>_close_now` | Jetzt schließen |
| `binary_sensor.<zone>_airing_worthwhile` | Lüften würde jetzt spürbar etwas bringen (Abstand ≥ 8, nicht kurz nach dem Lüften) |

## Events

Diese Events kannst du für Benachrichtigungen nutzen. Alle tragen `config_entry_id` und `zone` mit:

- `ventilation_coach_started`
- `ventilation_coach_close_now` (mit `reason`)
- `ventilation_coach_reminder` (10 Min nach „Jetzt schließen“)
- `ventilation_coach_finished` (Bilanz und Lernergebnis)
- `ventilation_coach_discarded` (im Nachlauf wieder geöffnet)

Eine fertige Automation mit Live-Update, Signal, Erinnerung und Bilanz liegt in [`examples/lueften_benachrichtigungen.yaml`](examples/lueften_benachrichtigungen.yaml).

## Dienste

- `ventilation_coach.set_time_constant`: τ (und optional die Lernläufe) einer Zone korrigieren.
- `ventilation_coach.reset_learning`: alles Gelernte einer Zone vergessen.

## Entwicklung

```bash
pip install -r requirements_test.txt
pytest
```

`tests/test_model.py` und `tests/test_engine.py` testen die Logik mit einer simulierten Zone. Die übrigen Tests prüfen Config-Flow und Einrichtung in Home Assistant. Ohne Home Assistant laufen die Logiktests auch mit `python3 scripts/run_pure_tests.py`.
