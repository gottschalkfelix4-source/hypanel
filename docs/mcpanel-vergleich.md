# Abgleich mit MCPanel

Vorlage: [gottschalkfelix4-source/mcpanel](https://github.com/gottschalkfelix4-source/mcpanel), Commit `7d303982cc937ca8d081d122a4f3b26feab419f1`. Am Minecraft-Projekt und seinen Diensten wurden keine Änderungen vorgenommen.

| MCPanel-Bereich | HyPanel 0.3.0 |
| --- | --- |
| Gestaltung | Kopfleiste, Steinrahmen, versenkte Felder, Pixel- und Rubik-Schrift, responsive Serverkarten |
| Server | Docker-Container, UDP-Port, CPU/RAM, Java ActiveProcessorCount, Power-Aktionen, Autostart, Absturz-Neustart, Klonen |
| Übersicht | Docker-Auslastung, 72 Stunden Messverläufe, aktive/bekannte Spieler, tatsächliche Welt-TPS, Aktivitäten und Aufträge |
| Konsole | Live-Stream, Fehlerfarben, Suche, Auto-Scroll, Befehlsverlauf, Download, Hytale-Geräteanmeldung |
| Mods | Upload, aktivieren/deaktivieren/entfernen, CurseForge-Hytale-Suche, Versionen und Updates mit Sicherheitsbackup |
| Dateien | Texteditor, JSON-Prüfung, Upload/Download/Umbenennen/Löschen, ZIP in neues Verzeichnis entpacken, SFTP |
| Konfiguration | Typisierte ServerName/MOTD/MaxPlayers/MaxViewRadius-Felder und JSON-Editor; unbekannte Felder bleiben erhalten |
| Backups | Daten/vollständig, konsistenter Stopp/Neustart, Archivprüfung und Sicherheitsbackup vor Restore, Anzahl-Rotation, Server-/Globalquoten |
| Spieler | Live-/Historienliste; Kick, Ban, Unban, Whitelist und Operator-Aktionen über Hytale-Konsole |
| Automation | Cron mit Zeitzone, Backup/Start/Stop/Neustart/Befehl/Downloader-Update, manuell ausführen, bearbeiten, pausieren |
| Zugriff | Administratoren/Mitglieder; 14 Serverrechte, eingeschränkte Delegation |
| Hytale-Accounts | Assistent je Server für Download, Geräteanmeldung, Profilauswahl, verschlüsselte Speicherung und Abmeldung; eigenes delegierbares Recht |
| Panel | Benutzer, API-Schlüssel, Aktivitäten, Host-Werte, Auftragsliste, lokale Panel-Sicherung |

## Hytale-Unterschiede und verbleibende Erweiterungen

Hytale nutzt Java 25 und QUIC über UDP. Minecraft-Loader, RCON, Minecraft-Modrinth-Pakete und der Minecraft-TCP-Subdomain-Proxy werden nicht verwendet. Updates laufen über den offiziellen Downloader bzw. Hytale-Updatebefehle. Das lokale Telemetrie-Plugin ersetzt Minecraft-Abfragen für Spieler und TPS.

Die MCPanel-Erweiterungen S3/Samba-Zweitablage, Webhook-Benachrichtigungen, KI-Assistent, vollständige Modpack-Vergleiche, periodische Mod-Updateprüfung und Panel-Backup-Cron sind noch nicht umgesetzt. Der Katalog installiert Plugins und Asset-Packs mit Hytale-manifest.json; beliebige Modpack-Archive werden nicht als installierbar ausgegeben.

## Betrieb

Benutzer, Server, Welten und Backups bleiben beim Update erhalten. Der neue Runner ergänzt beim Start HyPanelTelemetry.jar in Server/mods und kompiliert es gegen die vorhandene Hytale-JAR. Bei einer inkompatiblen API läuft der Server ohne Telemetrie weiter und meldet den Fehler.

SFTP: Port 2223, Benutzer `Panelname+Server-ID`, Panel-Passwort. Verbindungsdaten stehen im Dateimanager. Kein Shellzugang; jedes Konto sieht nur das gewählte Serververzeichnis. Schreiben braucht die Dateiberechtigung und einen gestoppten Server.

TPS: echte World.getTick-Fortschritte pro vergangener Sekunde, langsamste Welt. Keine Zieltickrate, keine simulierten Werte. Das Plugin öffnet keinen Netzwerkport; veraltete Daten verfallen nach 20 Sekunden.

CurseForge: eigenen API-Schlüssel unter Panel hinterlegen. Nur Hytale-Projekte, Größen-/Prüfsummenprüfung, Backup vor jeder Installation. Erforderliche Abhängigkeiten zuerst installieren. Minecraft-Modpacks funktionieren nicht in Hytale.

Backups: Standard 20 GiB je Server, 100 GiB global und 10 Archive. Quotenfehler erhalten bestehende Sicherungen. Panel-Sicherungen enthalten Zugangsdaten und keine Spielwelten; vertraulich aufbewahren. Wiederherstellungsanleitung liegt im Archiv.
