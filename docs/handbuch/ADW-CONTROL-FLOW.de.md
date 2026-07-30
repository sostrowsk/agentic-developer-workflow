# ADW-Kontrollfluss — Die sieben Phasen einfach erklärt

> Zielgruppe: Einsteiger ohne Programmierkenntnisse.
> Quelle: `docs/SPEC.de.md` (Stand 2026-07-14).

## Worum geht es überhaupt?

Der **Agentic Developer Workflow (ADW)** ist ein Programm, das eine Aufgabe
(ein „Issue", z. B. *„Baue eine Suchfunktion in die App ein"*) **vollautomatisch**
in fertigen, geprüften Code verwandelt — von der ersten Beschreibung bis zur
Auslieferung auf einen Testserver.

Man kann sich das wie eine **Baustelle** vorstellen:

| Rolle auf der Baustelle | Rolle im ADW | Wer macht das? |
|---|---|---|
| Bauleiter (organisiert alles, entscheidet nichts Fachliches) | Der **Orchestrator** — festes Programm | Deterministischer Code |
| Architekt (schreibt Bauplan) | Spec-Agent & Plan-Agent | KI (Fable 5) |
| Handwerker (bauen tatsächlich) | Build-Agents | KI (Opus 4.8) |
| Bauprüfer / TÜV (kontrollieren, bauen aber nie selbst) | Codex-Reviewer & finaler Reviewer | KI (Codex / Fable 5) |
| Checklisten & Messgeräte (immer gleiche Prüfungen) | **Gates** (automatische Tests) | Deterministischer Code |

**Der wichtigste Grundsatz:** *Kontrollfluss ist Code, nicht Prompt.*
Das heißt: **Wer wann drankommt, entscheidet ein festes Programm** — nicht die KI.
Die KI wird nur dort eingesetzt, wo Urteilsvermögen nötig ist (schreiben, bauen,
bewerten). Alles Wiederholbare (Tests starten, Ergebnisse weiterreichen, Schleifen
zählen) läuft als normaler Programmcode: kostenlos, zuverlässig, immer gleich.

Zwei weitere eiserne Regeln:

1. **Prüfer reparieren nie.** Wer einen Fehler findet, meldet ihn nur. Repariert
   wird immer von den Bau-Agenten — und jede Reparatur muss danach **erneut durch
   alle Prüfungen**. Keine Abkürzung, auch nicht für „Kleinigkeiten".
2. **Alles hat ein Limit.** Jede Schleife darf nur begrenzt oft wiederholt werden.
   Ist das Limit erschöpft — oder bringt eine Reparatur-Runde *gar nichts* mehr
   („Circuit-Breaker", wie eine Sicherung im Stromkasten) — bricht der Lauf
   kontrolliert ab und schreibt einen **Eskalations-Bericht**: Was wurde geschafft,
   was ist offen, woran lag es. Ein Mensch übernimmt dann.

---

## Der Weg durch die sieben Phasen

```
Issue ──▶ 1 Spec ──▶ 2 Plan+Kontrakt ──▶ [STOPP: Mensch genehmigt] ──▶ 3 Build
                                                                        │
              7 Push+CI ◀── 6 Finaler Review ◀── 5 Code-Review ◀── 4 Integration+E2E
                  │
                  ▼
          Grüne Pipeline + Staging = fertig ✔
```

Bei fast jeder Phase gibt es kleine **Rückkopplungsschleifen** (Fehler gefunden →
zurück zum Verursacher → nachbessern → erneut prüfen). Die Details folgen jetzt
Phase für Phase.

---

### Phase 1 — Spec: „Was soll überhaupt gebaut werden?"

**Eingabe:** das Issue — ein Text, entweder direkt eingetippt oder aus GitLab/GitHub geholt.

1. Der **Spec-Agent** (KI) liest das Issue und das Projekt und schreibt eine
   **Spezifikation** (`.adw/spec.md`): Ziel, was dazugehört, was ausdrücklich
   *nicht* dazugehört, und woran man erkennt, dass es fertig ist
   („Akzeptanzkriterien"). Er darf dabei **nur lesen und diese eine Datei
   schreiben** — bauen darf er nichts.
2. Der **Codex-Reviewer** (eine zweite, unabhängige KI) prüft die Spezifikation.
3. Findet er Mängel, gehen sie **an denselben Spec-Agenten zurück** — der behält
   sein „Gedächtnis" aus der ersten Runde (Session-Resume) und bessert nach.
4. Das wiederholt sich, bis der Prüfer **„ok"** sagt — höchstens **5 Runden**,
   und die Messlatte sinkt pro Runde (Details bei Phase 5).

> Wie ein Aufsatz, den ein Lektor so lange zurückgibt, bis er sauber ist —
> aber der Lektor schreibt nie selbst am Aufsatz mit.

### Phase 2 — Plan + Kontrakt: „Wie wird es gebaut?"

1. Der **Plan-Agent** (KI) verwandelt die Spezifikation in einen
   **Schritt-für-Schritt-Bauplan** (`.adw/plan.md`) und einen **Kontrakt**
   (`.adw/contract.yaml`). Der Kontrakt ist wie eine verbindliche
   Steckdosen-Norm: Er legt exakt fest, wie die Teile (z. B. Oberfläche und
   Server-Logik) später zusammenpassen müssen — damit zwei getrennt arbeitende
   Teams am Ende keine inkompatiblen Teile abliefern.
2. Der Codex-Reviewer prüft Plan und Kontrakt **gemeinsam**, wieder in der
   Schleife bis „ok" (gleiche 5-Runden-Regel).
3. **Plan-Approval-Gate — der eingebaute STOPP:** Der Workflow **hält an**,
   speichert seinen kompletten Zustand und beendet sich. Jetzt liest ein
   **Mensch** den Plan und entscheidet. Erst der Befehl `adw approve` (bzw.
   `adw resume`) lässt den Lauf exakt an dieser Stelle weiterlaufen.
   (Wer der Automatik voll vertraut, kann den Stopp mit `--no-approval`
   abschalten.)

> Das ist der einzige geplante Punkt, an dem der Mensch *mitten im* Ablauf
> gefragt wird — bevor die teure Bauphase beginnt.

### Phase 3 — Build: „Jetzt wird gebaut."

1. Der **Dispatch** (festes Programm, keine KI) zerlegt den Plan in
   **Arbeitspakete** — z. B. „Frontend" (das Sichtbare) und „Backend" (die
   Logik dahinter). Jedes Paket bekommt eine eigene **Lane** (Spur):
   einen eigenen Arbeitsordner (Git-Worktree), eine eigene KI-Sitzung und
   eigene Netzwerk-Ports. Die Lanes können sich so **nicht gegenseitig
   in die Quere kommen**. (Ohne `--parallel` gibt es einfach nur eine Lane —
   gleicher Ablauf, eine Spur.)
2. In jeder Lane läuft der **Lane-Loop**:
   - Der **Build-Agent** (KI) schreibt Code — strikt nach Plan und Kontrakt.
   - Danach laufen die **Gates**: automatische Prüfungen (Formatierung,
     Code-Stil, Tests), fest im Projekt konfiguriert. Wie Kontrollstationen
     am Fließband.
   - **Rot?** Die Fehlermeldungen gehen als neue Aufgabe **an dieselbe
     KI-Sitzung** zurück — sie kennt ihren eigenen Code noch und bessert nach.
   - Das wiederholt sich, **maximal 10-mal**. Danach: Eskalations-Bericht,
     Abbruch, Mensch übernimmt.

### Phase 4 — Integration + E2E: „Passen die Teile zusammen?" (nur bei `--parallel`)

1. Ein festes Programm **verschmilzt** die Ergebnisse aller Lanes auf einen
   gemeinsamen Integrations-Zweig.
2. Dann läuft ein **End-to-End-Test** (Playwright): Ein Roboter klickt sich wie
   ein echter Benutzer durch die fertige Anwendung.
3. **Schlägt etwas fehl**, sortiert der **E2E-Triage-Agent** (KI, nur lesend)
   jeden Fehler der **verantwortlichen Lane** zu — wie ein Arzt, der nur
   diagnostiziert, aber nicht operiert.
4. Die betroffene Lane repariert (wieder über ihren normalen Lane-Loop aus
   Phase 3, inklusive aller Gates), dann wird **neu integriert und neu
   getestet**. Maximal **10 Runden**.

### Phase 5 — Codex-Code-Review: „Unabhängige Qualitätskontrolle."

1. Der **Codex-Reviewer** liest den gesamten neuen Code (nur lesend!) und
   liefert eine strukturierte Mängelliste: Was ist das Problem, wie schwer ist
   es (P1 = kritisch … P3 = klein), welche Lane ist zuständig, und ein
   **Reparatur-Vorschlag** (`remediation_plan`).
2. Ein festes Programm **verteilt** die Mängel anhand der Lane-Angabe an die
   richtigen Build-Agents.
3. Wichtig: Der Reparatur-Vorschlag ist eine **Empfehlung, kein Befehl**. Der
   Build-Agent prüft ihn gegen Spezifikation und Projektregeln und darf
   **begründet anders** reparieren — der Prüfer sieht den Code ja nur von außen.
4. Nach jeder Reparatur: alle Gates, dann erneutes Review — **bis „ok"**,
   höchstens **5 Runden**. Dabei sinkt die Messlatte pro Runde: Runde 1 behebt
   alle Mängel, Runde 2 nur noch kritische und mittlere (P1+P2), ab Runde 3 nur
   noch kritische (P1) — kleinere Punkte werden als bekannte Einschränkungen
   festgehalten statt endlos nachpoliert.
5. Damit der Prüfer nicht immer wieder dasselbe anmerkt, bekommt er ab Runde 2
   die **Mängelliste der Vorrunden samt Begründung** mit („behoben" bzw.
   „bewusst nicht übernommen, weil …").

### Phase 6 — Finaler Review + Triage: „Wurde wirklich das Richtige gebaut?"

Phase 5 fragte: *Ist der Code gut gemacht?* Phase 6 fragt: *Tut er das, was in
der Spezifikation steht?*

1. Der **finale Reviewer** (KI, strikt nur lesend) vergleicht die fertige
   Implementierung mit der Spezifikation aus Phase 1 und meldet Abweichungen —
   jede mit einer **Kategorie**.
2. Die **Triage** (festes Programm) sortiert nach Kategorie:
   - **`scope_gap`** — „Da fehlt etwas, das gar nicht im Plan stand" →
     wird als **Folge-Issue** notiert (Bericht für später). Es wird **nicht**
     automatisch neu gestartet, sonst würde der Lauf endlos wachsen.
   - **`implementation` / `trivial`** — „Gebaut, aber falsch/unsauber" →
     zurück in die zuständige Lane, normaler Reparatur-Zyklus mit allen Gates.
3. Maximal **3 Reparatur-Zyklen**, dann Eskalation.

### Phase 7 — Push + CI: „Ausliefern und beobachten."

1. Der fertige Zweig wird ins zentrale Repository **gepusht** (hochgeladen).
2. Dort startet automatisch die **CI-Pipeline** des Projekts (GitLab oder
   GitHub): Sie baut alles noch einmal neutral zusammen, testet und spielt das
   Ergebnis auf den **Staging-Server** (die Testumgebung) auf.
3. Der ADW **fragt alle 60 Sekunden nach dem Status** — bis alles grün ist,
   höchstens aber **45 Minuten**.
4. **Wird die Pipeline rot**, liest der **Log-Analyst** (KI, nur lesend) die
   Fehlerprotokolle, macht daraus strukturierte Befunde mit Lane-Zuordnung —
   und der Ablauf springt **zurück in Phase 3/4**: reparieren, prüfen, erneut
   pushen.
5. **Pipeline grün + Staging-Deploy grün → der Lauf ist erfolgreich beendet.**

---

## Die Sicherheitsnetze (gelten überall)

| Mechanismus | Was er bedeutet |
|---|---|
| **Limits** | Phase 3: max. 10 Fix-Iterationen · Phase 4: max. 10 Runden · Phase 5: max. 5 Review-Runden · Phase 6: max. 3 Zyklen · Phase 7: max. 45 min Warten |
| **Circuit-Breaker** | Löst eine Reparatur-Runde *nichts* auf → sofortiger Abbruch statt sinnlosem Weiterdrehen |
| **Eskalations-Bericht** | Bei jedem Abbruch entsteht `escalation.md`: was erreicht, was offen, warum — Übergabe an den Menschen |
| **Speicherpunkte** | Nach **jedem Phasenübergang** wird der komplette Zustand gespeichert. `adw resume` setzt nach Absturz, Pause oder erschöpftem KI-Kontingent **exakt dort** fort — wie ein Spielstand im Videospiel |
| **Strukturierte Übergaben** | Alle Prüfer melden Befunde in einem festen Datenformat (JSON). Lässt sich eine Antwort nicht sauber lesen, gilt sie als **Fehler** — lieber einmal zu viel nachfragen als ein falsches „ok" durchwinken |
| **Berechtigungen** | Jede KI bekommt nur die Werkzeuge, die ihre Rolle braucht: Prüfer dürfen nur lesen, Bau-Agenten nur in ihrem eigenen Ordner schreiben |

## Das Ganze in einem Satz

> Ein festes Programm führt eine Aufgabe durch sieben Stationen — Beschreiben,
> Planen (mit menschlicher Freigabe), Bauen, Zusammenfügen, zweimal unabhängig
> Prüfen, Ausliefern — und lässt KI nur dort arbeiten, wo Urteilsvermögen
> gebraucht wird, während jede Schleife ein Limit, jeder Befund einen festen
> Rückweg und jeder Abbruch einen Bericht hat.
