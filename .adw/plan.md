# Implementierungsplan — ADW Run Inspector: i18n (de/en) + `adw runs list`/`prune` + `trace:`-Config

Baut die letzten beiden offenen Schritte der GUI-SPEC (Schritt 12: i18n §7.5;
Schritt 13: Retention §4.5). Beide sind in `.adw/spec.md` ausformuliert; dieser
Lauf implementiert sie, er entwirft sie nicht neu. Bei Konflikten gilt die Spec.
Reale Gates: `uv run ruff check .` und `uv run pytest -x -q` (E4 —
GUI-SPEC-Abnahmepunkt 10 mit „flake8 + isort" ist veraltet und wird NICHT
befolgt).

Der Kontrakt `.adw/contract.yaml` pinnt die extern beobachtbare Fläche (CLI,
Exit-Codes, Config-Keys, Sprachauswahl, Worktree-/Gzip-Zusagen). Der Build baut
strikt dagegen. Interne Helper-Signaturen, Dictionary-Schlüssel und Markup sind
frei.

## Ausgangspunkt

- Die GUI wird in `adw/gui/app.py` über serverseitige Jinja-Templates
  (`run_list.html`, `run_detail.html`) ausgeliefert; die Detailansicht trägt
  zustandsrelevante Query-Parameter (`limit`, `offset`, `tools_offset`,
  `focus`, `raw_q`, `raw_type`).
- Alle GUI-Lesewege verwenden heute `events.jsonl`; `adw/gui/reader.py` ist
  inkrementell und byte-offset-basiert. Es gibt keinerlei Gzip-Behandlung.
- Die CLI basiert auf Typer in `adw/cli.py`; eine `runs`-Gruppe existiert nicht.
- Run-State liegt unter `.adw/runs/<run_id>/`, Worktrees unter
  `.adw/runs/<run_id>/trees/<lane>`, Snapshot-Refs unter `refs/adw/<run_id>/*`,
  Lane-Branches unter `adw/<run_id>/*`. Config wird in `adw/config.py` strikt
  validiert (bestehender `ConfigError`-Pfad).

## Workstream: backend

Single-Lane-Projekt (keine `frontend`-Lane); GUI-Handler und Templates gehören
zum Backend-Workstream. Empfohlene Reihenfolge: erst A (i18n, in sich
geschlossen), dann der Retention-Unterbau (Gzip-Reader → Datum → Prune-Kern)
und darauf die CLI und Config.

### A — i18n de/en (§7.5, AC A1–A6)

1. **Sprachkatalog `adw/gui/i18n.py` (A1).** Neues Modul mit einem
   `dict[str, dict[str, str]]` (`CATALOG`) für genau `de` und `en`, identische
   Schlüsselmengen, kein Wert unübersetzt (ausgenommen sprachneutrale Begriffe
   und technische Bezeichner: Run-IDs, Event-Typen, CLI-Namen). Schlüssel decken
   die gesamte übersetzbare UI-Chrome aus `run_list.html` und `run_detail.html`
   ab (Reiternamen, Spaltenköpfe, Labels, Navigations- und Hinweistexte —
   inklusive der heutigen englischen Strings „Answer", „Prompt", „Tools",
   „previous"/„more", „no trace", der Findings-Tabellenköpfe usw.).

2. **Sprachauswahl (A2/A3).** Funktion in `i18n.py`, die pro GUI-Request die
   Sprache in exakt dieser Reihenfolge bestimmt: `?lang=de|en` → Sprach-Cookie →
   erste unterstützte Sprache aus `Accept-Language` (gemäß Header-Reihenfolge) →
   `en`. Nicht unterstützte oder fehlende Werte einer Stufe fallen zur nächsten
   Stufe durch; letzter Fallback immer `en`. NUR eine explizite GÜLTIGE
   `?lang=`-Auswahl signalisiert dem Handler, das Sprach-Cookie auf diese
   Sprache zu setzen (A3); ungültige Query-Werte setzen kein Cookie.

3. **Handler-Integration `adw/gui/app.py` (A4).** In `run_list_page` und
   `run_detail_page` die Sprache aus dem `Request` bestimmen, das passende
   Katalog-Dict (plus aktuellen Sprachcode) in den Template-Context reichen und
   bei expliziter gültiger `?lang=`-Auswahl das Cookie auf der `HTMLResponse`
   setzen. Nur UI-Chrome kommt aus dem Katalog; Inhalte (`node.label`, Payloads,
   Artefakt-Bodies, Gate-Output, `_raw_view`-Text) bleiben unberührt und rendern
   sprachunabhängig byteidentisch. Der `?lang=`-Parameter darf die bestehende
   Fenster-/Fokus-Logik (`offset`, `tools_offset`, `focus`, `limit`, `raw_*`)
   nicht verändern. Die JSON-API bleibt fachlich unverändert; i18n betrifft die
   gerenderte HTML-Chrome.

4. **Templates konsumieren den Katalog (A4/A5).** `run_list.html` und
   `run_detail.html` ersetzen die fest verdrahteten Chrome-Strings durch
   Katalog-Zugriffe (über den in den Context gereichten Dict). `<html lang>`
   spiegelt die gewählte Sprache. Im Header ein **Sprachwechsel-Link** (A5): er
   zeigt auf dieselbe Ansicht mit umgeschaltetem `?lang=`, erhält ALLE für die
   Ansicht relevanten Query-Parameter (insb. `offset`, `tools_offset`, `focus`,
   `limit`, `raw_q`, `raw_type`) und die Knotenauswahl und ändert nur die
   Sprache. Kein Umschalter darüber hinaus (Deferred).

5. **Regressionstests A (`tests/test_gui_language.py` bleibt unverändert grün,
   E3).** Neue Tests (eigene Datei, z. B. `tests/test_i18n.py`):
   - **Paritäts-Test (A6):** läuft `CATALOG["de"]` gegen `CATALOG["en"]` —
     keine Schlüssel-Differenz, kein Wert unübersetzt im Sinne von A1.
   - **Auswahlreihenfolge (A2):** `?lang` schlägt Cookie, Cookie schlägt
     `Accept-Language`, `Accept-Language` schlägt Default; unbekannte Werte
     fallen durch; ein Request ganz ohne Sprachangabe rendert vollständig
     englisch.
   - **Cookie (A3):** `?lang=de` setzt das Cookie; ein Folge-Request ohne `lang`
     rendert deutsch; ein ungültiger `?lang=`-Wert setzt kein Cookie.
   - **Umfang (A4):** deutscher Request übersetzt die Chrome, lässt aber
     Inhalte (Prompt-/Agent-/Findings-/Gate-/Payload-Text) byteidentisch.
   - **Zustandserhalt (A5):** der Header-Wechsel-Link trägt `offset`,
     `tools_offset`, `focus` und die Knotenauswahl unverändert weiter, ändert
     nur `lang`.

### B — Retention-Unterbau

6. **Transparenter Gzip-Reader (C6).** `adw/gui/reader.py` (bzw. die davon
   abhängigen Lesewege) liest `events.jsonl` UND `events.jsonl.gz` mit derselben
   fachlichen Ausgabe: dieselben Events in derselben Reihenfolge, dieselbe
   Ereigniszahl, dieselbe `bad_line`-/`seq_gap`-Behandlung, dieselben abgeleiteten
   Ansichten. Gzip ausschließlich über die Standardbibliothek (E7). Liegen beide
   Dateien vor, ist `events.jsonl` maßgeblich (konsistent zu C5). Der Byte-Offset-
   Tail von `EventReader` bleibt für den unkomprimierten Fall wie heute; für den
   `.gz`-Fall darf der vollständige dekomprimierte Inhalt gelesen werden — ohne
   das bestehende `events.jsonl`-Verhalten (inkl. Live-Tail-Semantik) zu ändern.
   Alle GUI-Lesewege, die heute `run_dir / "events.jsonl"` auflösen
   (`_read_events`, `_list_runs`, der SSE-Tail, die Diff-/Events-Routen),
   erkennen die `.gz`-Alternative über dieselbe `_contained`-Absicherung.
   Run-Erkennung und -Auflösung bleiben auch für Läufe intakt, die nur State
   oder nur eines der beiden Logformate haben; ein komprimierter Lauf liefert
   weiterhin seine Snapshot-Events und damit die Diff-Ansichten über die
   erhaltenen Refs.

7. **Kanonisches Lauf-Datum (B4).** Ein Modul der Retention-Logik (z. B.
   `adw/retention.py`) ermittelt pro Lauf ein deterministisches UTC-Datum:
   primär der Zeitstempel des Start-Ereignisses aus dem Event-Log (`.jsonl` oder
   `.gz`), Fallback die mtime des persistierten State im Laufordner, ersatzweise
   die des Laufordners — ein Datum ist damit immer bestimmbar. Offset-Zeitstempel
   werden umgerechnet, naive als UTC interpretiert; Datei-mtimes sind
   Epoch-basiert. Tie-Breaker bei Gleichstand: Run-ID lexikografisch. Dasselbe
   Datum dient Anzeige, `--keep`-Sortierung und `--older-than`-Prüfung; dieselbe
   Lauf-Aufzählung (Run-ID, Phase, Datum, Ereigniszahl, Log-Größe/-Format)
   verwenden `runs list`, manuelles und automatisches Pruning identisch.

8. **Prune-Kern (C1–C5, C7).** In `adw/retention.py`:
   - **Aufzählung + Auswahl (C1):** Läufe des Repos auflisten, nach kanonischem
     Datum sortieren, die neuesten `N` schützen, die übrigen vom ältesten zum
     neuesten als Kandidaten; `--older-than` filtert über
     `run_time <= now_utc - DAYS*24h` mit einmal pro Aufruf ermitteltem
     `now_utc`, Gleichheit an der Grenze zählt als alt genug.
   - **Prune-Fähigkeit (C2):** nur State in `done`/`escalated`; jeder andere
     Kandidat wird mit Run-ID + Phase als übersprungen berichtet, vollständig
     erhalten.
   - **Schutz uncommitteter Worktrees (C4):** vor der ersten Mutation ALLE
     registrierten Worktrees unterhalb des Kandidaten-Laufordners
     inventarisieren (alle Lanes, nicht eine einzelne bekannte) und deren Status
     prüfen. Hat IRGENDEIN Worktree uncommittete Änderungen, wird der gesamte
     Lauf übersprungen (Run-ID + Grund); kein Worktree gewaltsam entfernt, kein
     Bestandteil gelöscht; andere sichere Kandidaten laufen weiter.
   - **Löschendes Pruning (C3, C7):** pro prune-fähigem Lauf in fester
     Reihenfolge — erst alle Worktree-Registrierungen über die Git-Worktree-
     Verwaltung (nutzt/erweitert `adw/worktrees.py`, KEIN rmtree der Trees),
     dann die Snapshot-Refs `refs/adw/<run_id>/*`, zuletzt der Laufordner. Der
     Laufordner wird nie entfernt, solange nicht alle Worktree-Registrierungen
     sauber behandelt sind. Lane-Branches `adw/<run_id>/*` bleiben erhalten. Ein
     Teilfehler lässt Erreichtes bestehen und benennt den erreichten Zustand
     (welche Schritte fehlten); erneuter `prune` setzt sicher fort — jeder
     Schritt ist idempotent, bereits entfernte Worktrees/Refs sind kein Fehler,
     ein teilweise bearbeiteter Lauf bleibt über sein Laufverzeichnis Kandidat.
   - **`--gzip` (C5):** pro prune-fähigem Kandidaten ausschließlich
     `events.jsonl` → `events.jsonl.gz`, atomar (Tempdatei im selben Verzeichnis
     → Validierung → `os.replace`), Quelle erst danach entfernt. „Bereits
     komprimiert" NUR wenn `.gz` existiert UND `.jsonl` fehlt; liegen beide vor,
     ist `.jsonl` maßgeblich und wird neu komprimiert, die alte `.gz` ersetzt.
     Eine korrupte `.gz` gilt nie als Ergebnis. Erhalten: Laufordner, State,
     Worktrees samt Registrierung, alle Snapshot-Refs, Lane-Branches (E10).

### C — CLI und Config

9. **CLI-Gruppe `runs` (`adw/cli.py`, B1–B3, C1, C7).** Neue Typer-Gruppe
   `runs` mit genau den Unterkommandos `list` und `prune`:
   - `adw runs list [--repo PATH]` — zeigt pro erkanntem Lauf mindestens Run-ID,
     Phase, Datum, Ereigniszahl, Log-Größe (zählt `.jsonl` wie `.gz`); Legacy-
     Läufe mit nicht ermittelbaren Werten sichtbar als unbekannt. Exit `0` auch
     ohne Läufe; nicht vorhandenes/nicht verwendbares `--repo` → klare Meldung,
     Exit `1`. Ohne `--repo` gilt wie bei den bestehenden Kommandos das
     aktuelle Verzeichnis.
   - `adw runs prune [--repo PATH] [--keep N] [--older-than DAYS] [--gzip]` —
     ruft den Prune-Kern (Task 8), gibt pro Kandidaten Run-ID + Ergebnis
     (gelöscht / komprimiert / bereits komprimiert / übersprungen mit Grund).
     Exit `0` bei vollständigem Lauf (Sicherheits-Skips sind kein Fehler); Exit
     `1` bei ungültigem Repo oder unsicher abgebrochener Löschung/Kompression;
     Exit `2` bei ungültigem `N`/`DAYS` (nichtnegative Ganzzahl) OHNE jede
     Änderung an Run-Daten. Genau diese Flächen — keine zusätzlichen Flags,
     keine interaktive Rückfrage, kein Default, der mehr löscht als `--keep 20`
     (E5).

10. **`trace:`-Config (`adw/config.py`, D1).** Neues optionales `TraceConfig`
    (`enabled: bool` strict, Default `true`; `keep_runs` nichtnegative Ganzzahl
    strict, Booleans zählen nicht, Default `20`) am `AdwConfig`. Fehlender Block/
    fehlende Keys → Defaults. Ungültige Werte über den bestehenden `ConfigError`-
    Pfad mit klarer Meldung, bevor ein Lauf startet.

11. **Konfigurierbares Ereignis-Logging (`adw/events.py`, D2, E6).** Die EINE
    bewusste Ausnahme: der Emitter respektiert `trace.enabled`. Bei `false` wird
    kein `events.jsonl` angelegt/erweitert; Lauf, State, Phasen und Resume
    bleiben unberührt. KEINE Disabled-Markierung, KEINE Emitter-Warnung dafür.
    Fail-open bei echten internen Fehlern, `_disabled_runs`-Mechanismus, Locking,
    Schema und `seq`-Vergabe bleiben unverändert; `adw/snapshots.py` eingefroren.
    Verdrahtung an der Emitter-Konstruktion in `adw/cli.py` (der Config-Wert ist
    dort verfügbar), konsistent auf allen Pfaden, die einen Emitter bauen —
    neuer Lauf, `resume` und `approve` — ohne die `EventEmitter`-Fail-open-
    Grenze zu verletzen.

12. **Automatisches Pruning (`adw/cli.py` + `adw/retention.py`, D3).** Nach einem
    erfolgreich in Phase `done` abgeschlossenen Lauf (der normale Rückkehrpfad
    von `_execute`) wird löschend gepruned, sofern `trace.keep_runs > 0`; der
    Wert ist die Zahl der geschützten neuesten Läufe, ohne Altersgrenze. Gleiche
    Inventarisierung, Kandidatenauswahl, Terminalitätsprüfung, Dirty-Worktree-
    Sicherung und Löschreihenfolge wie manuelles löschendes Pruning (C2–C4, C8).
    `keep_runs: 0` deaktiviert es; eskalierte/unterbrochene/auf Freigabe
    wartende Läufe lösen keines aus. Fail-open: ein Fehler ändert weder die
    persistierte Phase `done` noch den Erfolgs-Exit-Code des Laufs, wird aber
    sichtbar in der Kommando-Ausgabe gemeldet; die schrittweise Fehler-
    Invariante aus C7 gilt, ein späterer Aufruf setzt sicher fort.

### Regressionstests B/C (mind. das Genannte, DoD)

13. Tests auf der beobachtbaren Oberfläche (CLI, Config, Dateisystem, Git, HTTP)
    in echten Temp-Repos:
    - exaktes Behalten von `N` Läufen (C8); Schutz nicht-terminaler Läufe
      (übersprungen + benannt, C2);
    - Entfernen eines Laufs MIT registriertem Worktree — danach kein Eintrag in
      `git worktree list`, keine Waise laut `git worktree prune --dry-run`,
      Lane-Branch erhalten (C3);
    - ein Lauf mit MINDESTENS ZWEI Lane-Worktrees, darunter ein Dirty-Worktree
      in der nicht zuerst gefundenen Lane → gesamter Lauf übersprungen; im
      sauberen Fall alle Worktrees waisenfrei entfernt (C4);
    - vollständiges Überspringen bei uncommitteten Änderungen (C4);
    - deterministische Auswahl bei Datums-Gleichstand und bei Legacy-Läufen ohne
      Start-Ereignis (B4);
    - `--older-than` an der exakten Tagesgrenze (Gleichheit = alt genug) und bei
      Zeitstempeln mit unterschiedlichen UTC-Offsets (C1/B4);
    - Fortsetzen eines teilweise fehlgeschlagenen löschenden Prunings durch
      erneuten Aufruf (C7);
    - Erhalt von Refs UND Worktree bei `--gzip` (C5/E10); Gzip-Roundtrip durch
      Reader und GUI (C6);
    - unterbrochener Kompressionszustand (`.jsonl` und `.gz` gleichzeitig bzw.
      korrupte `.gz`) — Quelle maßgeblich, erneutes `--gzip` repariert (C5);
    - Defaults und Validierung von `trace.enabled`/`trace.keep_runs` (D1);
      ausbleibendes Logging bei `enabled: false` (D2);
    - automatisches Pruning nur nach erfolgreichem Lauf; `keep_runs: 0`
      deaktiviert es; ein scheiterndes Auto-Pruning lässt den Lauf konsistent
      auf `done` + Erfolgs-Exit-Code (D3);
    - `list`: Exit `0` ohne Läufe, Exit `1` bei kaputtem `--repo`, sichtbare
      Unbekannt-Werte für Legacy-Läufe (B2/B3).

    Keine Tests außerhalb der Acceptance Criteria und der Definition of Done.
    Richtwert: rund 22–30 neue Tests für A und B zusammen.

### Verifikation und Scope-Kontrolle

14. Gates ausführen (`uv run ruff check .`, `uv run pytest -x -q`) und den
    finalen Diff auf die Invarianten prüfen:
    - `adw/snapshots.py` unverändert; `adw/events.py` nur um den
      config-gesteuerten Abschalter verändert;
    - keine Übersetzung dynamischer Inhalte, keine dritte Sprache, keine neue
      Laufzeit-Dependency, keine GUI-Seite für `runs list`;
    - keine zusätzlichen Prune-Flags, keine Löschung von Lane-Branches;
    - Fensterung, DOM-Deckel, Performance-Measures, Limits, Circuit-Breaker,
      Review-Loop-Policy und Phasenreihenfolge unverändert;
    - kein `flake8`, `isort` oder `black` (E4).

## Definition of Done

- Alle Acceptance Criteria A1–A6, B1–B4, C1–C8, D1–D3 gelten und sind durch
  Regressionstests auf der beobachtbaren Oberfläche belegt.
- Die i18n-Regression deckt Dictionary-Parität, Auswahl per Query/Cookie/
  `Accept-Language`, englischen Default, unangetastete Inhalte und Zustandserhalt
  ab; `tests/test_gui_language.py` bleibt unverändert grün.
- Die Retention-Regression deckt mindestens die in Task 13 gelisteten Fälle ab.
- Keine neuen Laufzeit-Dependencies; `adw/snapshots.py`, Fensterung, DOM-Deckel,
  Performance-Measures, Limits, Circuit-Breaker, Review-Loop-Policy und
  Phasenreihenfolge ohne Verhaltensänderung.
- Gates grün: `uv run ruff check .` und `uv run pytest -x -q`.

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
