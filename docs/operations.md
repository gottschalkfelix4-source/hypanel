# Betrieb und Wiederherstellung

## Panel aktualisieren

Appdata mit dem vorhandenen Backupverfahren sichern, Image aktualisieren und ausschließlich `HyPanel` neu erstellen. Spielcontainer laufen bei Panelneustarts weiter und werden anhand ihrer IDs wieder erkannt. Keine Volumes oder Serververzeichnisse löschen.

```sh
docker compose pull
docker compose up -d
```

Panel und Runner müssen die gleiche Releaseversion verwenden. Bereits laufende Spielcontainer behalten ihr Image bis zum nächsten Start über das Panel.

## Hytale aktualisieren

Im laufenden Server `update check`, `update download`, dann über die Panel-Schaltfläche mit vollständigem Backup `update apply --confirm`. Der Runner übernimmt den Exit-Code-8-Neustart. Alternativ Server stoppen und unter Einstellungen den Downloader erneut starten; HyPanel sichert den vorhandenen Stand vorher vollständig. Bestätige gegebenenfalls eine erneute Geräteanmeldung.

## Backup und Restore

Backups sind ZIP-Dateien unter `/data/backups/<server-id>`. Bei der Erstellung werden Logs, Caches, Updaterdaten, interne Backups und Anmeldedaten ausgelassen. Datenbackups enthalten Spielstände, Konfiguration und Mods; vollständige Backups zusätzlich Binaries/Assets. Ein laufender Server wird für die Sicherung geordnet gestoppt und danach gestartet. Authentifizierungsdateien verbleiben bei Wiederherstellung im Serververzeichnis.

Restore benötigt einen gestoppten Server. Das Archiv wird zuerst auf gefährliche Pfade, Symlinks und eine maximale entpackte Gesamtgröße von 32 GiB geprüft. Vor dem Ersetzen entsteht eine vollständige Sicherheitskopie. Gesicherte Weltverzeichnisse werden ersetzt, damit neuere Chunkdateien nicht unbemerkt mit alten Spielständen vermischt werden.

Die Aufbewahrung gilt pro Server und löscht bei neuen Sicherungen die ältesten ZIP-Dateien oberhalb des eingestellten Limits. Eine Wiederherstellung kann deshalb eine alte Sicherung aus der Aufbewahrung verdrängen; das ausgewählte Archiv wird vorab vollständig entpackt. Gelöschte Server behalten ihr Sicherheitsbackup im Appdata-Backupverzeichnis. Diese Archive sind nach dem Löschen über das Hostdateisystem erreichbar.

Für Disaster Recovery das **gesamte Appdata** inklusive `panel.db`, `identities` und Server-Authentifizierungsdateien offline sichern. ZIP-Spielbackups ersetzen kein vollständiges Panelbackup.

## Zugang zurücksetzen

Falls alle Zugangsdaten verloren sind, über das lokale Unraid-Terminal einen vorhandenen Administrator gezielt zurücksetzen. Niemals `panel.db` entfernen; dadurch gingen Rechte und Serverzuordnungen verloren.

```sh
docker exec -it HyPanel python3 -m hypanel.admin reset-password admin
```

Der Befehl fragt das neue Passwort verdeckt ab und widerruft die alten Sessions.

## Diagnose

```sh
docker logs --tail 100 HyPanel
docker inspect --format '{{.State.Health.Status}}' HyPanel
curl --fail http://127.0.0.1:8095/api/health
docker ps --filter label=io.hypanel.instance=default
```

Einrichtung: `/data/setup.key` nur beim ersten Start ohne Benutzer. Dockerfehler: Socket-Einbindung, Runner-Image, Portkonflikt, verfügbaren Speicher und exakten Hostdatenpfad prüfen. Hytale-Anmeldung: `auth status`; verschlüsselte Persistenz mit `auth persistence Encrypted`. Die Maschinenidentität darf beim Wiederherstellen auf demselben Host nicht versehentlich gelöscht werden.

## Ports

8095/TCP ist der Hostport des Panels. Spielcontainer veröffentlichen ihren jeweils konfigurierten Port per UDP selbst; keine großen UDP-Portbereiche im Paneltemplate erforderlich. Routerfreigaben werden absichtlich nicht automatisch verändert.
