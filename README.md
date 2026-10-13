# HyPanel

Ein selbst gehostetes Hytale-Serverpanel für Docker und Unraid. Jeder Spielserver läuft in einem eigenen Container mit eigenem UDP-Port, persistenten Daten und begrenzten CPU-/RAM-Ressourcen. Die deutschsprachige Weboberfläche benötigt keinen separaten Webserver oder zusätzliche Datenbank.

## Funktionen

| Bereich | Funktionen |
| --- | --- |
| Server | Mehrere Server, Erstellen, Bearbeiten, Klonen, Löschen mit Sicherheitsbackup |
| Betrieb | Start, geordnetes Stoppen, Neustart, erzwungenes Beenden, Autostart, begrenzter Neustart nach Absturz |
| Installation | Offizieller Hytale-Downloader mit Geräteanmeldung, ZIP-Import lokaler Serverdateien, Release-/Pre-Release-Kanal |
| Konsole | Live-Ausgabe per SSE, Befehle, Log-Download, Hytale-Geräteanmeldung und verschlüsselte Auth-Persistenz |
| Ressourcen | Docker-CPU-/RAM-Limits, gemessene CPU-/RAM-Nutzung, Status und Exit-Code |
| Dateien | Verzeichnisbrowser, Upload mit Fortschritt, Download, Text-/JSON-Editor, Ordner erstellen, verschieben, umbenennen, löschen |
| Mods | JAR-/ZIP-Upload, aktivieren, deaktivieren, entfernen |
| Welten | Vorhandene Welten, Weltkonfiguration, Zugriff auf Weltdateien |
| Spieler | Konsolenaktionen für Liste, Kick, Ban, Whitelist und Operatorrechte; originale Rechte-/Whitelist-/Ban-Dateien |
| Backups | Daten- oder vollständige ZIP-Backups, Download, Aufbewahrung, Wiederherstellung mit vorherigem Sicherheitsbackup |
| Zeitpläne | Cron mit IANA-Zeitzone, Start/Stop/Neustart, Backup, Befehl, Downloader-Update; persistente Termine und Ergebnisse |
| Benutzer | Administratoren, Mitglieder, Serverzuweisung mit Lese-/Operatorrechten, Passwortwechsel, Deaktivierung und Session-Widerruf |
| Nachvollziehbarkeit | Aktivitätenprotokoll, System-/Dockerstatus, Healthcheck, OpenAPI unter `/docs` |
| Unraid | DockerMan-Template, Compose, persistentes Appdata, UID/GID 99:100 für Spielserver |

## Unraid installieren

1. [Template herunterladen](https://raw.githubusercontent.com/gottschalkfelix4-source/hypanel/main/unraid/hypanel.xml).
2. Die XML auf Unraid unter `/boot/config/plugins/dockerMan/templates-user/my-HyPanel.xml` speichern.
3. In **Docker → Add Container** das Template **HyPanel** auswählen.
4. `Appdata` und `Host Data Path` müssen **denselben absoluten Hostpfad** enthalten, standardmäßig `/mnt/user/appdata/hypanel`.
5. Container starten und `http://UNRAID-IP:8095` öffnen.
6. Den einmaligen Einrichtungsschlüssel über die Unraid-Terminalkonsole abrufen:

```sh
docker exec HyPanel cat /data/setup.key
```

Den Schlüssel in der Einrichtungsmaske eingeben und ein Administratorkonto mit mindestens zwölf Passwortzeichen erstellen. Der Schlüssel wird anschließend gelöscht. Es gibt keine zusätzliche Tokenabfrage im laufenden Panelbetrieb.

### Docker Compose

```sh
git clone https://github.com/gottschalkfelix4-source/hypanel.git
cd hypanel
cp .env.example .env
# HYPANEL_HOST_DATA in .env auf den tatsächlichen absoluten Hostpfad setzen.
docker compose up -d
```

Das Image wird von GitHub Actions nach GHCR veröffentlicht. Für einen lokalen Build:

```sh
docker build -t ghcr.io/gottschalkfelix4-source/hypanel:latest .
docker compose up -d --pull never
```

Unterstütztes Containerziel: **Linux x86_64**, passend zum Unraid-Host. Java 25 und der offizielle Linux-Downloader sind im Image enthalten; Hytale-Spiel- und Serverdateien sowie Kontodaten werden nicht veröffentlicht.

## Ersten Hytale-Server starten

1. **Server erstellen**: Name, freier UDP-Port (z. B. 5520), mindestens 4096 MiB RAM und CPU-Limit festlegen.
2. Unter **Einstellungen → Hytale herunterladen** den offiziellen Downloader starten. Im Installationsprotokoll den Gerätecode ablesen und auf der offiziellen Hytale-Seite mit dem eigenen lizenzierten Konto bestätigen.
3. Alternativ ein ZIP mit `Server/HytaleServer.jar`, optional `Server/HytaleServer.aot`, und `Assets.zip` importieren. Vorhandene Konfigurationen und Welten werden beim Paketimport erhalten.
4. Server starten, unter **Konsole → Hytale anmelden** den zweiten Gerätecode bestätigen.
5. **Anmeldung speichern** führt `auth persistence Encrypted` aus. Die Maschinenidentität bleibt je Server über Containerneustarts erhalten. Klone erhalten neue Identitäten und keine Anmeldedaten.
6. Im Hytale-Client `UNRAID-IP:UDP-PORT` verwenden. Für Internetspieler den jeweiligen **UDP-Port** im Router zum Unraid-Host weiterleiten.

Ein laufender Java-Prozess beweist keine erfolgreiche Hytale-Anmeldung und keine erfolgreiche Spieleranmeldung. Prüfe den Auth-Status in der Konsole und die Verbindung mit einem Hytale-Client.

## HTTPS und bestehender Reverse Proxy

Der Panel-Endpunkt spricht HTTP. Der vorhandene Reverse Proxy übernimmt TLS und Zertifikate.

```dotenv
HYPANEL_PUBLIC_URL=https://hytale.example.com
HYPANEL_COOKIE_SECURE=true
HYPANEL_TRUSTED_PROXIES=192.168.188.10
```

Die URL muss dem Browser-Origin exakt entsprechen, ohne zusätzlichen Pfad. Für SSE Proxy-Buffering deaktivieren und einen ausreichend langen Read-Timeout konfigurieren. Spielserver verwenden UDP/QUIC und werden nicht über den HTTP-Reverse-Proxy geleitet.

## Betriebsmodell

Das Panel ist für einen Docker-Host ausgelegt. Der Zugriff auf den Docker-Socket ist für das Erstellen der Spielcontainer erforderlich und verleiht dem Panel Docker-Administratorrechte. Mitglieder erhalten keinen direkten Socketzugriff. Spielcontainer erhalten ausschließlich ihr Serververzeichnis, eine schreibgeschützte Maschinenidentität und kein Panel-Appdata oder Docker-Socket. Sie laufen als UID 99/GID 100 ohne Linux-Capabilities, mit schreibgeschütztem Root-Dateisystem und Prozesslimit.

Dateiänderungen, Mods, Import, Ressourcenänderungen, Klonen und Restore verlangen einen gestoppten Server. Backups stoppen einen laufenden Server geordnet und starten ihn danach wieder. Plane diese Unterbrechung ein. Datenbackups lassen große Server-/Assetdateien aus; vollständige Backups enthalten sie. Authentifizierungsdateien sind in beiden Varianten ausgeschlossen. Sichere das gesamte Panel-Appdata zusätzlich mit deinem vorhandenen Unraid-Backupverfahren.

Das Panel verwaltet Serverprozesse und Konsolenbefehle. Eine strukturierte Online-Spielerabfrage ist ohne Hytale-Query-Plugin nicht verfügbar; die Spielerliste wird aus der echten Konsolenantwort abgelesen. Befehle und Konfigurationsschemas hängen von der installierten Hytale-Version ab. Die Konsole und der Originaldatei-Editor bieten Zugriff auf die tatsächlich verfügbaren Funktionen.

Weitere Details: [Betrieb und Wiederherstellung](docs/operations.md), [Architektur](docs/architecture.md), [Validierung](docs/validation.md).

## Entwicklung

```sh
python -m venv .venv
# Virtuelle Umgebung aktivieren.
pip install -e '.[test]'
pytest -q
node --check frontend/app.js
```

Lokaler Panelstart ohne Container:

```sh
HYPANEL_DATA=./data python -m hypanel
```

Docker-Funktionen benötigen einen erreichbaren Docker-Daemon und den korrekten `HYPANEL_HOST_DATA`-Pfad. Für produktiven Betrieb das Containerimage verwenden. Das Backend läuft bewusst mit einem Worker, damit lokale Operationensperren und der Scheduler genau einmal existieren.

## Quellen und Lizenz

Hytale-Integration nach dem [offiziellen Server Manual](https://support.hytale.com/hc/en-us/articles/45326769420827-Hytale-Server-Manual) und dem [Server Provider Authentication Guide](https://support.hytale.com/hc/en-us/articles/45328341414043-Server-Provider-Authentication-Guide), geprüft am 13.10.2026. Hytale ist eine Marke von Hypixel Studios; dieses Projekt ist unabhängig und enthält keine Hytale-Serverdateien. HyPanel-Quellcode: MIT.
