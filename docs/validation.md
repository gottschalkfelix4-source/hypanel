# Validierung

Diese Datei wird vor dem Release mit den tatsächlich ausgeführten Prüfungen ergänzt. Maßgeblich sind Testausgabe, GitHub-Actions-Run und Laufzeitnachweise. Kein Login oder Spielerbeitritt wird aus einem erfolgreichen Containerstart abgeleitet.

Automatisierte Tests decken Einrichtungsschutz, Auth/CSRF/Origin, Session-Widerruf, letzte-Admin-Schutz, Serverrechte, Pfad-/Archivgrenzen, Dateioperationen, Backup/Restore, Klonen ohne Anmeldedaten, Import und Cron/Zeitzonen ab.
