# Spezifikation — ADW Run Inspector: i18n (de/en) und `adw runs list` / `adw runs prune` + `trace:`-Config

Baut die letzten beiden offenen Schritte der Umsetzungsreihenfolge aus
`docs/GUI-SPEC.md`: Schritt 12 (i18n, §7.5, Abnahme 9) und Schritt 13
(`adw runs list` / `adw runs prune` + `trace:`-Config, §4.5, Abnahme 8).
Beide sind dort bereits ausformuliert; dieser Lauf implementiert sie, er
entwirft sie nicht neu. Wo `docs/GUI-SPEC.md` Abnahmepunkt 10 noch
"flake8 + isort" nennt, ist der Spec-Text veraltet und wird nicht befolgt:
Die realen Gates sind `uv run ruff check .` und `uv run pytest -x -q`.

## Goal

1. Der Run Inspector zeigt seine UI-Chrome wahlweise auf Deutsch oder
   Englisch. Ein Modul `adw/gui/i18n.py` haelt die Sprach-Dictionaries, die
   Sprache wird pro Request deterministisch bestimmt, Englisch bleibt der
   Default, Inhalte werden nie uebersetzt.
2. Retention wird bedienbar: `adw runs list` macht sichtbar, wann Pruning
   faellig ist; `adw runs prune` gibt den tatsaechlich dominierenden
   Speicheranteil frei (die Git-Worktrees in den Laufordnern — 96 % der
   595 MB), ohne uncommittete Arbeit zu verwerfen oder verwaiste
   Worktree-Registrierungen zu hinterlassen; `--gzip` komprimiert stattdessen
   die Event-Logs und behaelt den Lauf vollstaendig.
3. Ereignis-Logging und automatische Retention sind ueber einen `trace:`-Block
   in `.adw/config.yaml` steuerbar.

## Scope

- Neues Modul `adw/gui/i18n.py`: `dict[str, dict[str, str]]` fuer genau `de`
  und `en`, plus die request-getriebene Sprachauswahl fuer die GUI.
- GUI-Templates/-Handler: uebersetzte Chrome konsumieren, Sprachwechsel-Link
  im Header, Sprach-Cookie lesen/setzen, `?lang=`-Auswahl beachten.
- `adw/gui/reader.py` (und die davon abhaengigen Lesewege): `events.jsonl`
  UND `events.jsonl.gz` transparent lesen — neue Arbeit, heute existiert
  keinerlei Gzip-Behandlung; Gzip ueber die Standardbibliothek.
- `adw`-CLI: neue Gruppe `runs` mit `list` und `prune`, exakt mit der
  CLI-Flaeche aus §4.5.
- Retention-Logik: Lauf-Aufzaehlung, Auswahl nach `--keep`/`--older-than`
  ueber ein kanonisches Lauf-Datum, loeschendes Pruning (Laufordner +
  Snapshot-Refs + alle im Laufordner liegenden Worktrees via
  Git-Worktree-Verwaltung), `--gzip` als behaltende Form,
  Skip-und-Benennen-Regeln, Ausgabe des Entfernten/Komprimierten.
- Config: `trace:`-Block (`enabled`, `keep_runs`) mit Defaults und
  Validierung; automatisches Pruning nach erfolgreichem Lauf; der Emitter in
  `adw/events.py` respektiert `trace.enabled` (die eine bewusste Ausnahme
  von dessen Einfrierung, E6).

## Non-goals

- Keine Aenderung an `adw/snapshots.py` — bleibt vollstaendig eingefroren.
- Keine Aenderung an `adw/events.py` jenseits des config-gesteuerten
  Abschalters: Fail-open-Verhalten (inkl. `_disabled_runs`-Mechanismus),
  Locking, Ereignisschema und `seq`-Vergabe bleiben unangetastet.
- Keine Uebersetzung von Inhalten: Prompts, Agent-Ausgaben, Findings,
  Artefakt-Koerper, Gate-Output, Tool-Ein-/Ausgaben, rohe Event-Payloads —
  auch nicht "nur die Ueberschriften".
- Keine dritte Sprache, keine i18n-Bibliothek, kein gettext, keine
  `.po`-Dateien, keine neue Laufzeit-Dependency.
- Keine GUI-Seite fuer `runs list` (reines CLI-Kommando, E8).
- Keine zusaetzlichen Flags fuer `runs prune`, keine interaktive Rueckfrage,
  kein Default, der mehr loescht als `--keep 20` (E5).
- Keine Aenderung an Fensterung, DOM-Deckel oder den `performance`-Measures
  aus dem Vorlauf.
- Keine Migration oder Nachverarbeitung bestehender Laufordner ausser durch
  die beiden neuen Kommandos.
- Keine Aenderung an Limits, Circuit-Breaker, Review-Loop-Policy oder
  Phasenreihenfolge.
- Kein `flake8`, `isort` oder `black` als Dependency, Konfiguration oder
  Kommando (E4).

## Acceptance criteria

### A — i18n de/en

**A1 — Sprachkatalog.** `adw/gui/i18n.py` stellt ein
`dict[str, dict[str, str]]` fuer genau `de` und `en` bereit. Beide
Dictionaries haben dieselbe Schluesselmenge, und kein Wert ist
unuebersetzt (kein deutscher Wert ist lediglich der englische Text oder
umgekehrt) — ausgenommen sprachneutrale Begriffe und technische Bezeichner
(Run-IDs, Event-Typen, CLI-Namen).

**A2 — Auswahlreihenfolge und englischer Default.** Die Sprache wird pro
GUI-Request ausschliesslich in dieser Reihenfolge bestimmt:
`?lang=de|en` → Sprach-Cookie → erste unterstuetzte Sprache aus
`Accept-Language` → `en`. Nicht unterstuetzte oder fehlende Werte einer
Stufe lassen die Auswahl zur naechsten Stufe weiterfallen; der letzte
Fallback ist immer `en`. Ein Request ohne jede Sprachangabe rendert
vollstaendig englisch; `tests/test_gui_language.py` bleibt OHNE Aenderung
gruen, einschliesslich `"Antwort" not in html` am Default-Request.

**A3 — Cookie-Verhalten.** Eine explizite Auswahl ueber `?lang=de|en` setzt
das Sprach-Cookie auf diese Sprache; nachfolgende Requests ohne `lang`
verwenden das Cookie gemaess A2.

**A4 — Uebersetzungsumfang.** Alle sichtbaren Elemente der UI-Chrome —
Labels, Reiternamen, Spaltenkoepfe, Navigations- und Hinweistexte — kommen
aus dem Sprachkatalog. Inhalte werden in keiner Sprache angefasst: Prompts,
Agent-Ausgaben, Findings, Artefakt-Koerper, Gate-Output, Tool-Ein- und
-Ausgaben sowie rohe Event-Payloads rendern unabhaengig von der gewaehlten
Sprache byteidentisch.

**A5 — Sprachwechsel ohne Zustandsverlust.** Der Header traegt einen Link
zum Wechsel zwischen Deutsch und Englisch. Der Ziel-Link erhaelt alle fuer
die Ansicht relevanten Query-Parameter und aendert nur die Sprache;
insbesondere bleiben der gefensterte Ausschnitt (`offset`, `tools_offset`,
`focus`) und die Knotenauswahl erhalten.

**A6 — Dictionary-Paritaet als Regressionstest.** Ein Test laeuft die
beiden Dictionaries gegeneinander: kein Schluessel fehlt auf einer Seite,
kein Wert ist unuebersetzt im Sinne von A1.

### B — `adw runs list`

**B1 — CLI-Oberflaeche.** `adw --help` fuehrt eine `runs`-Gruppe auf; deren
Unterkommandos sind genau `list` und `prune` mit den Oberflaechen
`adw runs list [--repo PATH]` und
`adw runs prune [--repo PATH] [--keep N] [--older-than DAYS] [--gzip]`.
Ohne `--repo` gilt wie bei den bestehenden Kommandos das aktuelle
Verzeichnis.

**B2 — Sichtbare Lauf-Daten.** `adw runs list` zeigt pro erkanntem Lauf
mindestens: Run-ID, Phase, Datum, Ereigniszahl, Log-Groesse — genug, um zu
sehen, wann Pruning faellig ist. Ereigniszahl und Log-Groesse
beruecksichtigen `events.jsonl` wie `events.jsonl.gz`. Das Datum ist das
kanonische Lauf-Datum nach B4. Ein bei einem Legacy-Lauf nicht
ermittelbarer Wert (etwa die Ereigniszahl ohne Log) wird sichtbar als
unbekannt dargestellt, statt den Aufruf scheitern zu lassen.

**B3 — Exit-Codes.** Ein erfolgreicher `list`-Aufruf endet mit Exit-Code
`0`, auch ohne Laeufe. Ein nicht vorhandenes oder nicht verwendbares
`--repo` fuehrt zu einer verstaendlichen Fehlermeldung und Exit-Code `1`.

**B4 — Kanonisches Lauf-Datum.** Fuer Anzeige (`list`), Sortierung
(`--keep`) und Alterspruefung (`--older-than`) gilt ueberall dasselbe
Datum: der Zeitstempel des Start-Ereignisses aus dem Event-Log des Laufs
(`events.jsonl` oder `events.jsonl.gz`). Fehlt dieses oder ist es
ungueltig (Legacy-Lauf, `trace.enabled: false`), gilt als Fallback die
Aenderungszeit des persistierten State im Laufordner, ersatzweise die des
Laufordners selbst — ein Datum ist damit immer bestimmbar. Alle
Zeitstempel werden auf UTC normalisiert: Zeitstempel mit Offset werden
umgerechnet, naive (offsetlose) Zeitstempel als UTC interpretiert;
Datei-Zeitstempel sind Epoch-basiert und damit bereits UTC. Gleichstaende
werden deterministisch ueber die Run-ID (lexikografisch) aufgeloest, sodass
zwei Implementierungen bei identischem Bestand identische Laeufe
auswaehlen.

### C — `adw runs prune`

**C1 — Kandidatenauswahl.** Ohne `--keep` gilt `20`. `--keep N` schuetzt
die neuesten `N` Laeufe des Repos; die uebrigen werden vom aeltesten zum
neuesten bearbeitet. Ist `--older-than DAYS` angegeben, werden unter den
nicht geschuetzten Laeufen nur solche bearbeitet, deren Datum mindestens
`DAYS` Tage zurueckliegt; juengere bleiben erhalten. Die Alterspruefung
ist der eindeutige Vergleich `run_time <= now_utc - DAYS * 24h`:
`now_utc` wird genau einmal pro Aufruf ermittelt, `run_time` ist das
UTC-normalisierte kanonische Datum nach B4, und Gleichheit an der Grenze
zaehlt als alt genug. Neu/alt, Reihenfolge und Alter bemessen sich
ausschliesslich am kanonischen Lauf-Datum samt Tie-Breaker aus B4. `N` und `DAYS` akzeptieren nur nichtnegative
Ganzzahlen; ungueltige Werte fuehren ohne jede Veraenderung an Run-Daten
zum ueblichen CLI-Fehler (Exit-Code `2`).

**C2 — Nicht-terminale Laeufe werden nie gepruned.** Prune-faehig ist ein
Lauf nur, wenn sein persistierter State in `done` oder `escalated` ist —
sein State ist die Resume-Grundlage. Jeder ausgewaehlte Lauf in einer
anderen Phase bleibt vollstaendig erhalten und wird mit Run-ID und Phase
als uebersprungen ausgegeben, nicht stillschweigend ausgelassen. Das gilt
gleichermassen fuer loeschendes Pruning, `--gzip` und automatisches
Pruning.

**C3 — Loeschendes Pruning.** Ohne `--gzip` entfernt `prune` pro
prune-faehigem Lauf: den Laufordner `.adw/runs/<run_id>/`, alle
Snapshot-Refs `refs/adw/<run_id>/*` und SAEMTLICHE registrierten
Git-Worktrees unterhalb des Laufordners (`.adw/runs/<run_id>/trees/<lane>`
— pro Lane einer) — letztere ueber die Git-Worktree-Verwaltung statt per
rmtree. Danach fuehrt `git worktree list` keinen Worktree des Laufs mehr
auf und `git worktree prune --dry-run` meldet fuer keinen eine Waise. Die
Lane-Branches (`adw/<run_id>/*`) bleiben erhalten.

**C4 — Schutz uncommitteter Worktrees.** Hat IRGENDEINER der Worktrees
eines Kandidaten uncommittete Aenderungen, wird der gesamte Lauf
uebersprungen: kein Worktree wird gewaltsam entfernt (auch kein sauberer
desselben Laufs) und kein anderer Bestandteil dieses Laufs geloescht
(Laufordner, State, Event-Log, Refs, Branches bleiben erhalten). Der Lauf
wird mit Run-ID und Grund als uebersprungen ausgegeben; andere sichere
Kandidaten werden weiterhin bearbeitet. `prune` verwirft niemals fremde
Arbeit.

**C5 — `--gzip` als behaltende Form.** Mit `--gzip` wird pro prune-faehigem
Kandidaten ausschliesslich `events.jsonl` mittels Standardbibliothek zu
`events.jsonl.gz` komprimiert. Das Schreiben ist atomar: erst in eine
temporaere Datei im selben Verzeichnis, nach Validierung per Replace auf
`events.jsonl.gz`; die unkomprimierte Quelle entfaellt erst danach. Ein
Abbruch waehrend der Kompression hinterlaesst nie den Zustand "korrupte
`.gz` gilt als Ergebnis": Als "bereits komprimiert" gilt ein Lauf NUR,
wenn `events.jsonl.gz` existiert UND `events.jsonl` fehlt. Liegen beide
vor (unterbrochener frueherer Versuch), ist `events.jsonl` massgeblich
und der Lauf wird neu komprimiert, wobei die alte `.gz` ersetzt wird.
Erhalten bleiben ausdruecklich: Laufordner, State, Worktrees samt
Registrierung, saemtliche Snapshot-Refs (`refs/adw/<run_id>/*`) und
Lane-Branches — ohne Refs verloere genau dieser behaltene Lauf seinen
Diff-Reiter.

**C6 — Transparenter Gzip-Reader.** Alle Lesewege des Run Inspectors, die
`events.jsonl` lesen, lesen alternativ `events.jsonl.gz` mit derselben
fachlichen Ausgabe: dieselben Events in derselben Reihenfolge, dieselbe
Ereigniszahl, dieselbe Behandlung ungueltiger Records und Sequenzluecken,
dieselben daraus gebauten Ansichten. Liegen beide Dateien vor, liest der
Reader `events.jsonl` (konsistent zu C5: die unkomprimierte Datei ist
dann massgeblich). Ein per `--gzip` komprimierter Lauf bleibt in der GUI
vollstaendig lesbar, einschliesslich seiner Diff-Ansichten ueber die
erhaltenen Refs.

**C7 — Ausgabe und Exit-Codes.** `prune` gibt pro bearbeitetem Kandidaten
Run-ID und Ergebnis aus: geloescht, komprimiert, bereits komprimiert oder
uebersprungen (mit Grund). Ein vollstaendig ausgefuehrter Aufruf endet mit
Exit-Code `0`; sicherheitsbedingte Ueberspringungen sind berichtete
Ergebnisse, kein Fehler. Ein ungueltiges Repo oder ein Fehler, durch den
eine Loeschung/Kompression nicht sicher abgeschlossen werden kann, fuehrt
zu Exit-Code `1`.

Fehler-Invariante des loeschenden Prunings (schrittweise, keine
Transaktion ueber Git und Dateisystem): Pro Lauf wird in fester
Reihenfolge gearbeitet — erst alle Worktree-Registrierungen ueber die
Git-Worktree-Verwaltung, dann die Snapshot-Refs, zuletzt der Laufordner.
Insbesondere wird der Laufordner nie entfernt, solange nicht alle seine
Worktree-Registrierungen sauber behandelt wurden. Scheitert ein spaeterer
Schritt, bleiben bereits erfolgreich ausgefuehrte fruehere Schritte
bestehen; die Ausgabe benennt fuer den betroffenen Lauf den erreichten
Zustand (welche Schritte fehlten). Ein erneuter `prune`-Aufruf setzt
diesen Zustand sicher fort: Der teilweise bearbeitete Lauf ist weiterhin
Kandidat, bereits entfernte Worktrees oder Refs sind dabei kein Fehler,
die verbleibenden Schritte werden nachgeholt.

**C8 — Retention-Ergebnis.** Sind alle Laeufe terminal, sicher loeschbar
und alt genug, laesst `prune --keep N` exakt die neuesten `N` Laufordner
zurueck; fuer jeden geloeschten Lauf existieren danach weder Snapshot-Refs
noch eine verwaiste Worktree-Registrierung, seine Lane-Branches existieren
weiterhin.

### D — `trace:`-Config und automatische Retention

**D1 — Konfigurationsvertrag.** `.adw/config.yaml` akzeptiert optional:

```yaml
trace:
  enabled: true      # Default true; false = gar kein Ereignis-Log
  keep_runs: 20      # 0 = nie automatisch prunen
```

Fehlender Block oder fehlende Keys ergeben die genannten Defaults.
`trace.enabled` akzeptiert nur einen echten Boolean; `trace.keep_runs` nur
eine nichtnegative Ganzzahl (Booleans zaehlen nicht als Ganzzahl).
Ungueltige Werte werden ueber den bestehenden Config-Fehlerpfad mit klarer
Meldung abgewiesen, bevor ein Lauf startet — nicht stillschweigend
fehlangewendet.

**D2 — Konfigurierbares Ereignis-Logging.** Bei `trace.enabled: true`
bleibt das Logging unveraendert. Bei `trace.enabled: false` wird kein
`events.jsonl` angelegt oder erweitert; Lauf, State, Phasen und
Resume-Faehigkeit bleiben unberuehrt. Der config-gesteuerte Abschalter
erzeugt weder eine Disabled-Markierung noch eine Emitter-Warnung; das
bestehende Fail-open-Verhalten bei echten internen Fehlern bleibt
unveraendert.

**D3 — Automatisches Pruning.** Nach einem erfolgreich (Phase `done`)
abgeschlossenen Lauf wird automatisch loeschend gepruned, sofern
`trace.keep_runs > 0`; der Wert ist die Zahl der geschuetzten neuesten
Laeufe. Es gelten dieselben Regeln und Zusagen wie beim manuellen
loeschenden Pruning (C2–C4, C8). `keep_runs: 0` deaktiviert automatisches
Pruning vollstaendig; ein eskalierter, unterbrochener oder auf Freigabe
wartender Lauf loest keines aus.

Automatisches Pruning ist fail-open: Ein Fehler dabei aendert das Ergebnis
des soeben abgeschlossenen Laufs nicht — die persistierte Phase bleibt
`done`, der Exit-Code des Laufs bleibt der Erfolgs-Exit-Code. Der Fehler
wird sichtbar gemeldet (verstaendliche Meldung in der Kommando-Ausgabe des
Laufs), nicht verschluckt. Es gilt die schrittweise Fehler-Invariante aus
C7: bereits ausgefuehrte Schritte bleiben bestehen, der erreichte Zustand
wird benannt, und ein spaeterer Prune-Aufruf (manuell oder automatisch)
setzt ihn sicher fort.

## Definition of Done

- Alle Acceptance Criteria A1–A6, B1–B4, C1–C8, D1–D3 gelten und sind durch
  Regressionstests auf der beobachtbaren Oberflaeche (CLI, Config,
  Dateisystem, Git, HTTP) belegt.
- Die i18n-Regression deckt ab: Dictionary-Paritaet (A6), Auswahl per
  Query, Cookie und `Accept-Language`, englischer Default, unangetastete
  Inhalte, Zustandserhalt beim Sprachwechsel.
  `tests/test_gui_language.py` bleibt unveraendert gruen.
- Die Retention-Regression deckt mindestens ab: exaktes Behalten von `N`
  Laeufen; Schutz nicht-terminaler Laeufe (uebersprungen und benannt);
  Entfernen eines Laufs MIT registriertem Worktree in einem echten
  Temp-Repo — danach weder Eintrag in `git worktree list` noch Waise laut
  `git worktree prune --dry-run`, Lane-Branch erhalten; ein Lauf mit
  MINDESTENS ZWEI Lane-Worktrees, darunter ein Dirty-Worktree in der nicht
  zuerst gefundenen Lane — der gesamte Lauf wird uebersprungen, beim
  sauberen Fall werden alle Worktrees waisenfrei entfernt; vollstaendiges
  Ueberspringen bei uncommitteten Worktree-Aenderungen; deterministische
  Auswahl bei Datums-Gleichstand und bei Legacy-Laeufen ohne
  Start-Ereignis (B4); `--older-than` an der exakten Tagesgrenze
  (Gleichheit zaehlt als alt genug) und bei Zeitstempeln mit
  unterschiedlichen UTC-Offsets; Fortsetzen eines teilweise
  fehlgeschlagenen loeschenden Prunings durch erneuten Aufruf (C7);
  Erhalt von Refs UND Worktree bei `--gzip`;
  Gzip-Roundtrip durch Reader und GUI; ein unterbrochener
  Kompressionszustand (`events.jsonl` und `events.jsonl.gz` gleichzeitig
  vorhanden bzw. korrupte `.gz`) — Quelle bleibt massgeblich, erneutes
  `--gzip` repariert; Defaults und Validierung von
  `trace.enabled`/`trace.keep_runs`; ausbleibendes Logging bei
  `enabled: false`; automatisches Pruning nur nach erfolgreichem Lauf;
  ein scheiterndes automatisches Pruning laesst den abgeschlossenen Lauf
  konsistent auf `done` und dem Erfolgs-Exit-Code.
- Richtwert: rund 22–30 neue Tests fuer A und B zusammen.
- Keine neuen Laufzeit-Dependencies; `adw/snapshots.py`, Fensterung,
  DOM-Deckel, Performance-Measures, Limits, Circuit-Breaker,
  Review-Loop-Policy und Phasenreihenfolge ohne Verhaltensaenderung.
- Gates gruen: `uv run ruff check .` und `uv run pytest -x -q`
  (Abnahmepunkt 10 der GUI-SPEC mit "flake8 + isort" ist veraltet und wird
  nicht befolgt, E4).

## Deferred (deliberately not built)

Bindet auch den Review-Loop: Ein Finding, das einen dieser Punkte oder
einen vorentschiedenen Punkt (E1–E10) fordert, wird abgewiesen und mit
Begruendung dokumentiert, nicht umgesetzt.

- Retention nach Groesse statt nach Anzahl/Alter; Kompression von etwas
  anderem als `events.jsonl`.
- Ein Sprachumschalter jenseits des Header-Links (Nutzerprofil,
  persistente Einstellung ueber das Cookie hinaus); Uebersetzung von
  Inhalten; eine dritte Sprache.
- `runs list` als GUI-Seite; Suche, Sortierung oder Filter darin.
- Ein Prune-Vorschaumodus (`--dry-run`) oder eine Undo-Funktion.
- Loeschen von Lane-Branches (`adw/<run_id>/*`) und Aufraeumen von
  Worktrees BEHALTENER Laeufe — eigener Vorgang. NICHT deferred ist der
  Worktree des Laufs, den `prune` ohnehin loescht: er liegt im Laufordner
  und ist davon nicht trennbar (C3, E9).
