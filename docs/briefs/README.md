# ADW-Briefe der GUI-Redesign-Reihe (0.22.0 – 0.26.0)

Die fünf Issue-Briefe, aus denen die Releases 0.22.0 bis 0.26.0 entstanden sind —
je ein autonomer ADW-Lauf mit `--gates none`, alle ohne Eskalation.

| Brief | Release | These |
|---|---|---|
| [1 — Fundament](redesign-1-fundament.md) | 0.22.0 | Farb-, Schrift- und Abstands-Tokens, Dark Mode, Phasenband als maßstäbliche Zeitachse |
| [2 — Wahrheit](redesign-2-wahrheit.md) | 0.23.0 | Kennzahlen über alle CLI-Spannen, drei benannte Zeitgrößen, Run-Liste mit Sortierung und Filter |
| [3 — Arbeitsfeld](redesign-3-arbeitsfeld.md) | 0.24.0 | Trace-Baum nach oben, Zusammenfassungen zugeklappt darunter, Timeline-Beschriftung aus dem Balken |
| [4 — Tastatur](redesign-4-tastatur.md) | 0.25.0 | Bedienbarer Baum mit einem Tab-Halt, Fokusindikator, eingelöstes Registerkarten-Muster |
| [5 — Nutzlast](redesign-5-nutzlast.md) | 0.26.0 | Teilantwort am vorhandenen Header, Panes bei Bedarf — Seite 44,7 % kleiner |

## Warum sie hier liegen

Nicht als Historie — die steht im Changelog —, sondern als **Vorlage**. Die
Struktur hat fünfmal hintereinander getragen, während die Läufe davor mehrfach
eskalierten. Wiederverwendbar ist vor allem:

- **Ausgangslage mit gemessenen Zahlen**, nicht mit Beschreibungen. Jede
  Behauptung vor dem Start im Code oder im Browser geprüft.
- **Nummerierte Vorentscheidungen (E1, E2, …)** mit dem Zusatz „Diese Frage ist
  entschieden — kein Finding, auch nicht im Review-Loop". Der Codex-Loop kann
  ambivalente Fragen finden, aber nicht entscheiden; offene Fragen oszillieren
  bis zum Rundendeckel.
- **Ein Abschnitt „Bestand, der NICHT Gegenstand dieses Issues ist"** — verhindert,
  dass ein Lauf vorhandene Flächen abbaut, weil der Brief sie nicht erwähnt.
- **Deferred-Liste, die ausdrücklich den Review-Loop bindet.**
- **Messbare Akzeptanzkriterien, vor der Freigabe nachgerechnet** — mindestens
  einmal hätte ein AC nur durch Bruch einer E-Vorentscheidung grün werden können.
- **Toolchain-Fakten explizit** (`ruff` + `pytest`, nie flake8/isort/black).

Wer einen neuen Brief schreibt: Prämissen **unmittelbar vor dem Start** neu gegen
den gemergten Stand messen. In dieser Reihe hat das dreimal etwas gefunden, das
sonst als falsche Prämisse in den Lauf gegangen wäre.
