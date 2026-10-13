# Architektur

Browser → FastAPI :8080 → SQLite/WAL und Docker API → isolierte Hytale-Spielcontainer.

Ein Image enthält Panel, Java 25, Runner und offiziellen Downloader. `HYPANEL_MODE` wählt `panel`, `runner` oder `install`. Ein Installer ist ein separater kurzlebiger Container. Der Panelcontainer verwaltet nur Container mit passenden `io.hypanel.instance`- und `io.hypanel.server`-Labels; eine Namenskollision mit einem fremden Container wird abgewiesen.

## Daten

```text
/data/
  panel.db                 Benutzer, Sessions, Server, Rechte, Zeitpläne, Aktivitäten
  identities/<server-id>   persistente Maschinenidentität (nur Panel schreibbar)
  servers/<server-id>/
    Assets.zip
    start.sh
    Server/
      HytaleServer.jar
      config.json
      auth.enc / auth.key
      mods/
      universe/
  backups/<server-id>/     ZIP-Archive ohne Anmeldedaten
  uploads/                temporäre Importdateien
```

Docker-Bind-Mounts werden vom Docker-Host aufgelöst, nicht im Panelcontainer. Deshalb muss `HYPANEL_HOST_DATA` dem Hostpfad der `/data`-Einbindung entsprechen. Es werden keine Fremdcontainer gestoppt, verändert oder gelöscht.

## Rechte

Administratoren verwalten Server, Ressourcen, Installation, Restore, Zeitpläne und Benutzer. Mitglieder erhalten explizit `viewer` oder `operator` pro Server. Leser können ihre Serverdaten und Logs einsehen. Operatoren steuern den Prozess, senden Konsolenbefehle, ändern Dateien/Mods und erstellen Backups. Konsolenbefehle sind mächtig und werden nur vertrauenswürdigen Operatoren gewährt.

Passwörter verwenden Argon2id. Zufällige Sessions liegen nur als SHA-256-Hash in der Datenbank, gelten 24 Stunden und werden als HttpOnly/SameSite-Strict-Cookie übertragen. Schreiboperationen verlangen einen separaten CSRF-Wert sowie eine passende Origin-Prüfung. Passwort-/Rollenänderung und Deaktivierung widerrufen Sessions. Der letzte aktive Administrator ist gegen Deaktivierung geschützt.

## Prozess und Konsole

Der Runner startet Java als Unterprozess mit einer Liste von Argumenten. Ein lokaler Unix-Socket übernimmt einzelne Konsolenbefehle; Docker exec startet dafür nur den festen Python-Socketclient. Nutzerbefehle gelangen niemals in eine Shell. SIGTERM sendet `stop`; Docker wartet bis zu 60 Sekunden, bevor es bei Bedarf beendet. Der Java-Heap bleibt 1024 MiB unter dem Container-RAM-Limit.

Exit-Code 8 startet den Runner nach dem selektiven Einspielen der vorbereiteten Hytale-Binaries/Assets neu. Konfiguration, Auth, Mods und Welten werden erhalten. Ein normaler Exit beendet den Runner. Optionale Docker-Neustarts sind auf drei Absturzversuche beschränkt.

## Grenzen

CPU-/RAM-Limits sind Containerlimits. Speicherplatz wird vom Hostdateisystem bereitgestellt; ein dediziertes Unraid-Appdata-Backup und die Überwachung freien Platzes bleiben erforderlich. Hytale-/Pluginversionen bestimmen Konsolenbefehle und Konfigurationsschema. Der Panelcontainer ist aufgrund des Docker-Sockets ein vertrauenswürdiger Hostdienst. Das Projekt betreibt einen Host; verteilte Nodes oder Hosting-Abrechnung sind keine impliziten Funktionen der Docker-Serververwaltung.
