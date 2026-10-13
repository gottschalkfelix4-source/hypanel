# Validierung

Stand: 13.10.2026. Maßgeblich sind Testausgabe, GitHub-Actions-Run und Laufzeitnachweise. Kein Login oder Spielerbeitritt wird aus einem erfolgreichen Containerstart abgeleitet.

Automatisierte Tests decken Einrichtungsschutz, Auth/CSRF/Origin, Session-Widerruf, letzte-Admin-Schutz, Serverrechte, Pfad-/Archivgrenzen, Dateioperationen, Backup/Restore, Klonen ohne Anmeldedaten, Import und Cron/Zeitzonen ab.

Zusätzlich geprüft: API-Schlüssel, Ablauf/Widerruf, HTTP-Größenlimit, Containerisolation, Scheduler-Claim für überfällige Termine, Pausieren/Ändern/manuelles Ausführen, selektives Einspielen vorbereiteter Updates und Installieren eines Downloader-Pakets ohne Überschreiben von Konfiguration oder Auth.

## Browser

`scripts/browser-smoke.cjs` wurde mit Chromium gegen einen echten lokalen Panelcontainer ausgeführt: erste Einrichtung, Login, Server erstellen, Datei erstellen/JSON speichern, Backup erstellen, Zeitplan erstellen/pausieren/aktivieren/manuell ausführen, API-Schlüssel erstellen/widerrufen und Logout. Desktop 1440×1000 und Mobile 390×844 wurden als Screenshots visuell geprüft; keine JavaScript-Fehler und kein horizontaler Überlauf der Seite. Tabellen und Tabs scrollen bei Bedarf innerhalb ihrer Bereiche.

## Unraid

Auf dem Unraid-Host wurden Image-Build, Panelstart auf HTTP-Port 8095 und Installation von `my-HyPanel.xml` im DockerMan-Templateverzeichnis verifiziert. Die API antwortet per LAN mit HTTP 200; der Container besitzt einen erfolgreichen Healthcheck.

Ein Server mit den vorhandenen lokalen Hytale-Dateien wurde in einem isolierten Container gestartet: Java 25, UID/GID 99:100, RAM-Limit 4096 MiB, CPU-Limit vier Kerne und UDP-Port 5520. Die echte Hytale-Ausgabe meldete `Hytale Server Booted!` nach etwa 16 Sekunden. Welt und Assets wurden geladen; RAM betrug etwa 2,1 GiB. Konsolenbefehle `help` und `auth status`, gemessene Ressourcen, geordnetes Stoppen, Datenbackup und Wiederherstellung wurden über die Panel-API ausgeführt.

Ein separater Testcontainer startete den offiziellen Downloader bis zur tatsächlichen Hytale-Geräteanmeldung. Der Testserver und die temporären Testkonten wurden danach entfernt. Der vorbereitete Hytale-Server bleibt gestoppt erhalten; die Panel-Einrichtung ist für den Betreiber wieder geöffnet.

Beim echten Starttest wurden zwei Fehler behoben: Docker-Tmpfs benötigt ausdrücklich `exec` für die native QUIC-Bibliothek, und ein mit einer anderen JVM erzeugter AOT-Cache muss ohne Cache starten können. Diese Korrekturen sind im Runner enthalten.

## Veröffentlichung und verbleibende Betreiberaktionen

GitHub Actions prüft Tests, JavaScript-Syntax und Unraid-XML und veröffentlicht das Linux-amd64-Image nach GHCR. Der anonyme Zugriff auf das Image-Manifest wurde mit HTTP 200 verifiziert. Das öffentliche Repository enthält weder Server-/Assetdateien noch Zugangsdaten.

Die Hytale-Kontoanmeldung, ein vollständiger lizenzierter Downloader-Download und ein Spielerbeitritt mit dem Hytale-Client wurden nicht abgeschlossen. Dafür muss der Betreiber sein eigenes Hytale-Konto im offiziellen Gerätecodeverfahren bestätigen. Panelbetrieb und echter Hytale-Boot wurden unabhängig davon nachgewiesen. Routerfreigabe und externer HTTPS-Proxy wurden nicht eingerichtet; das Panel ist direkt im LAN erreichbar.
