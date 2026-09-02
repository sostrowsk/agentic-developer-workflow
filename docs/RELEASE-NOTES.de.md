# Run Inspector 0.9 → 0.16 — Release Notes

[English](RELEASE-NOTES.md) | **Deutsch**

Acht Releases, die aus dem Ereignis-Log eines ADW-Laufs eine bedienbare
Oberfläche machen: sichtbare Wartezustände, Zeitreise durch den Lauf-Zustand,
Prompt-Diffs, Recovery-Kommandos, Plan-Skelett, Änderungsumfang — und
Haltepunkte im Orchestrator selbst.

Der vollständige, maschinennahe Verlauf steht in [`../CHANGELOG.de.md`](../CHANGELOG.de.md);
dieses Dokument ordnet die acht Releases ein und begründet die Entscheidungen,
die nicht offensichtlich sind.

| | |
| --- | --- |
| Zeitraum | 20.–27. August 2026 |
| Releases | 8 (0.9.0 – 0.16.0) |
| Tests | 892 → 1062 (+170) |
| Codezeilen | +6111 in `adw/` und `tests/` |

## Überblick

| Version | Datum | Änderung | Neue Tests | Suite | CI |
| --- | --- | --- | ---: | ---: | --- |
| 0.16.0 | 27.08. | Konfigurierbare Haltepunkte | 27 | 1062 | grün |
| 0.15.0 | 26.08. | Änderungsumfang je Lane | 19 | 1027 | grün |
| 0.14.0 | 26.08. | Plan-Skelett im Trace | 21 | 1004 | grün |
| 0.13.0 | 26.08. | Recovery-Karte | 20 | 978 | hängt (GitHub-Störung) |
| 0.12.0 | 26.08. | Raw-Absprung + Prompt-Diff | 17 | 953 | grün |
| 0.11.0 | 26.08. | Kontext-Panel mit Zeitreise | 21 | 936 | grün |
| 0.10.0 | 26.08. | Dry-Run-Kennzeichnung, Sortierung | 8 | 915 | grün |
| 0.9.0 | 26.08. | Wartezustände unterscheidbar | 15 | 907 | grün |

**Gemeinsamer Nenner:** Sieben der acht Releases fügen dem Orchestrator kein
einziges Event hinzu. Alle neuen Ansichten sind Projektionen des Ereignis-Logs,
das die Detail-Antwort ohnehin lädt — additive Felder an
`GET /api/runs/{repo}/{run_id}`, read-only, ohne neue Route, ohne Persistenz,
ohne neue Laufzeit-Abhängigkeit.

## 0.16.0 — Haltepunkte als verallgemeinerte Approval

Eine optionale Liste `breakpoints:` in `.adw/config.yaml` hält den Lauf vor den
teuren, schwer umkehrbaren Schritten an: `before_integration` nach Abschluss
aller Build-Lanes, `before_push` nach dem finalen Review — vor *jeglicher*
CI-Arbeit, also auch vor Vorbereitung und Forge-Polling. Der Halt nutzt den
bestehenden Approval-Pfad: Phase `awaiting_approval`, Exit-Code 2, Fortsetzung
mit `adw approve`.

Bewusst kein neuer Phasenwert: welcher Haltepunkt wartet, steht im neuen
State-Feld `pending_breakpoint`. Das `Phase`-Modell, die Phasenleiste, die
Retention und die Recovery-Karte aus 0.13.0 bleiben dadurch unverändert — der
Halt bekommt seine Handlungsanweisung geschenkt. Halte sind idempotent über
Crash und `resume`; `--no-approval` überspringt sie mit, ein einziger Schalter
für „keine menschliche Freigabe in diesem Lauf". Ohne den Schlüssel verhält sich
alles wie bisher.

**Bekannte Einschränkung [P2], behoben in 0.16.3:** `_config_for_continuation()` lud
`.adw/config.yaml` bei jedem `resume` und `approve` neu, und der Run-State hielt
keinen Schnappschuss der aktiven Haltepunkte. Wer die Konfiguration während
eines laufenden Runs bearbeitete, konnte künftige Halte hinzufügen oder entfernen —
die Spezifikation verbietet Laufzeitänderungen, der Code erzwang sie nicht.
0.16.3 pinnt die wirksame Haltepunkt-Menge beim Run-Start in `pinned_breakpoints`.

## 0.15.0 — Änderungsumfang eines Laufs

Das Run-Detail zeigt nebeneinander, welche Dateien ein Lauf tatsächlich geändert
hat — je Lane, mit `+/-`-Zahlen pro Datei aus dem Vergleich ihres ersten und
letzten Snapshots — und den im Contract deklarierten Scope, so wie er dasteht.

Es wird **kein** automatisches Urteil gefällt. Der Grund steht in den Daten:
über 18 Contracts gemessen existiert `x-adw-scope` nur in acht, in
uneinheitlicher Form, und kein einziger nennt Dateien oder Pfadmuster. Eine
Markierung „im Scope" wäre geraten, nicht abgeleitet — also stehen die Fakten
nebeneinander und die Bewertung macht der Mensch. „Diff verfügbar, aber leer"
bleibt dabei von „kein Diff verfügbar" unterscheidbar: eine Lane mit nur einem
Snapshot behauptet nicht fälschlich, es habe keine Änderungen gegeben.

Beobachtbar als `change_scope` mit `lanes` und `declared_scope`; bestehende
Diff-Logik, keine neue Git-Operation.

## 0.14.0 — Plan-Skelett im Trace

Ist `plan.md` vorhanden, leitet das Run-Detail je `## Workstream:`-Abschnitt
eine read-only Liste der geplanten Aufgaben ab und stellt sie neben den Trace
derselben Lane — „geplant" und „geleistet" in einer Ansicht. Bei laufenden
Läufen wird damit erstmals sichtbar, was noch aussteht, statt nur was war.

Der Parser kennt genau zwei Regeln und **kein** Kennungsmuster: Abschnitt bis
zur nächsten `##`-Überschrift, Aufgabe = jede `###`-Zeile, Text wortgetreu. Das
ist keine Nachlässigkeit, sondern ein Messergebnis — über die Läufe hinweg
schreiben Pläne ihre Aufgaben als `B1 — …`, `1. …`, `A.1 — …` oder
`Aufgabe A — …`; ein Musterfilter hätte für die Mehrheit eine leere Liste
geliefert. Der Status bleibt bewusst grob auf Lane-Ebene: das Ereignis-Log trägt
kein Aufgabenfeld, jede Zuordnung einzelner Trace-Knoten zu Plan-Aufgaben wäre
geraten.

Beobachtbar als `plan_skeleton`; fehlt oder passt `plan.md` nicht, entfällt das
Skelett ersatzlos.

## 0.13.0 — Recovery-Karte am verursachenden Knoten

Braucht ein Lauf menschliches Eingreifen, nennt das Run-Detail genau ein
passendes nächstes Kommando als kopierbaren, POSIX-shell-sicheren Text — mit
echtem Repo-Pfad aus der Registry und echter `run_id`, nie dem URL-Slug. Pause
am Approval-Gate ergibt `adw approve`, eine abgebrochene Arbeitsphase
`adw resume`, ein endgültig eskalierter Lauf gar kein Kommando, sondern den
klaren Hinweis, dass ein neuer Lauf nötig ist.

Die Auswahl folgt `state.phase` — nicht dem `phase`-Feld des
`escalation`-Ereignisses. Das ist der Unterschied zwischen funktionierend und
nutzlos: `escalate()` setzt den Zustand final auf `escalated`, *bevor* das
Ereignis mit der Ursprungsphase fällt. Im Eskalationsfall zeigt die Karte Grund,
Phase und die unmittelbar vorausgehenden `limit.hit`- und
`circuit_breaker`-Ereignisse und verlinkt `escalation.md`, statt es zu
duplizieren. Die GUI bleibt strikt read-only: das Kommando wird angezeigt,
niemals ausgeführt.

**Zur CI:** Der Pipeline-Lauf dieses Releases hängt seit dem Push in der
GitHub-Queue (Störung mit Status „Partial System Outage"). Der Stand ist lokal
mit derselben Suite verifiziert und in den grünen Läufen von 0.14.0 und 0.15.0
vollständig enthalten.

## 0.12.0 — Absprung in den Raw-Log und Prompt-Diff

Jeder Span-Knoten springt in den Raw-Reiter, vorgefiltert auf seinen
Teilbaum-Bereich `[seq, end_seq]`. Der Reiter bekam dafür einen inklusiven
Seq-Bereichsfilter, serverseitig komponiert mit den vorhandenen Freitext-, Typ-
und Fensterfiltern; `total` bleibt die Treffermenge vor der Fensterung, eine
nicht-numerische Grenze ist inaktiv, ein umgekehrter Bereich ergibt eine
definierte leere Menge — nie ein 5xx. Das Aufheben entfernt nur den Bereich.

Der Prompt-Reiter eines `agent.run` zeigt zusätzlich einen Unified Diff gegen
den vorherigen Lauf desselben Agenten in derselben Lane — bei einer Fix-Runde
also genau den angehängten Findings-Block. Der Vorgänger wird strikt strukturell
gewählt: hat der unmittelbare Vorgänger keinen brauchbaren Prompt, ist die
Antwort „kein Vorgänger" und nicht etwa ein Diff gegen den vorletzten Lauf, der
stillschweigend das Falsche zeigen würde. Drei unterscheidbare Zustände: kein
Vorgänger, identischer Prompt, Diff.

Beobachtbar als `prompt_diff` und `previous_prompt_seq` an
`agent.run`-Knoten; Diff aus der Standardbibliothek `difflib`, die
`…/events`-Route unverändert.

## 0.11.0 — Kontext-Panel mit Zeitreise

Neben dem Detail-Pane zeigt eine Feldliste den Lauf-Zustand *zum Stand des
ausgewählten Knotens*: Phase, umgebende Runde mit `n/cap`, Anzahl der bis hier
getroffenen Limits und Circuit-Breaker, kumulierte Kosten, Zahl der Follow-ups.
Damit wird sichtbar, *warum* ein Knoten so ausging, ohne den Baum abzuklappern.

Der Cutoff eines Knotens ist seine eigene `seq`, bei Spans das Teilbaum-Maximum
`end_seq`; es zählen nur Ereignisse bis einschließlich Cutoff, sodass die
Knotenauswahl eine Zeitreise ist. Berechnet wird das in einem einzigen Durchlauf
über die seq-sortierten Ereignisse mit anschließender Binärsuche je Knoten —
nicht als Rescan pro Knoten, was bei großen Läufen die dokumentierte
Reaktionszeit gerissen hätte. Jeder fehlende Wert bleibt `null`, nie eine
erfundene Null.

Beobachtbar als `context` je Trace-Knoten und `latest_context` auf oberster
Ebene; `state.saved` unverändert.

## 0.10.0 — Trockenläufe unverwechselbar, Liste nach Dringlichkeit

Ein Trockenlauf trägt ein kurzes Label in seiner Listenzeile und ein Banner im
Detail-Kopf, das beim Scrollen im Trace am oberen Rand angeheftet bleibt — eine
inhaltsarme Simulation sieht sonst aus wie ein echter Lauf, dem Daten fehlen.
Abgeleitet wird das ausschließlich aus dem längst vorhandenen `dry_run`-Feld des
Start-Payloads, nie aus fehlenden Token-Daten.

Dazu die Korrektur einer Nebenwirkung aus 0.9.0: die Run-Liste sortiert jetzt
`awaiting_approval` vor `running` vor dem Rest. Vorher rutschte ausgerechnet der
Lauf, der auf einen Menschen wartet, unter die neueren fertigen. Innerhalb jeder
Gruppe bleibt „neueste zuerst".

## 0.9.0 — „arbeitet", „wartet", „wartet auf Menschen"

Drei bis dahin identisch dargestellte Situationen sind getrennt. Ein offener
`ci.wait`- oder `gate`-Span heißt im Trace-Baum `waiting` statt `running` —
dieselbe Unterscheidung, die die Timeline längst zeichnete, stimmt jetzt in
beiden Ansichten überein. Ein an einem Approval-Gate pausierter Lauf meldet
`awaiting_approval`, auch während sein Run-Span noch offen ist, und die
Phasenleiste zeigt die wartende Fachphase als `awaiting` statt `active`.

Hervorgehoben wird am stärksten der eine Zustand, in dem ein Mensch handeln
muss. Alles daran ist aus dem vorhandenen Log abgeleitet; ein Lauf ohne Trace
fällt auf seine State-Phase zurück, ein beendeter Lauf behält seinen terminalen
Status unangetastet.

## Herkunft dieser Releases

Alle acht Änderungen wurden vom ADW-Orchestrator gegen sein eigenes Repository
umgesetzt — je ein Lauf über Spec, Plan und Contract mit doppelter
Autorenschaft, Build mit Test-First-Gates, Codex-Review, finalem Review und CI.
Die Vorlage war eine Übertragung aus der Cobot-Programmierung: Teach-Pendants
trennen seit jeher „arbeitet" von „wartet", zeigen den Programmbaum samt
Ausführungszeiger, halten Variablen zum aktuellen Schritt bereit und bieten beim
Fehler eine Handlungsoption statt nur einer Meldung.

Nicht alles lief glatt, und das gehört zum Bild: Ein Lauf eskalierte an einer
falschen Tatsachenbehauptung im Issue-Brief, zwei weitere an einer
GitHub-Störung während der CI-Phase, einer stürzte mit einem SDK-Fehler ab und
wurde am Checkpoint fortgesetzt. Die betroffenen Stände wurden lokal mit
derselben Suite verifiziert; die Ursachen stehen in den jeweiligen
Release-Commits.
