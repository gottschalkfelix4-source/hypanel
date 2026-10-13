package io.hypanel;

import com.hypixel.hytale.server.core.plugin.JavaPlugin;
import com.hypixel.hytale.server.core.plugin.JavaPluginInit;
import com.hypixel.hytale.server.core.universe.Universe;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.HytaleServer;
import com.hypixel.hytale.server.core.auth.ServerAuthManager;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;

/** Local file telemetry. No HTTP listener, credentials, commands or external network. */
public final class HyPanelTelemetry extends JavaPlugin {
    private ScheduledExecutorService executor;
    private final Map<String, long[]> previous = new HashMap<>();
    public HyPanelTelemetry(JavaPluginInit init) { super(init); }

    @Override protected void start() {
        executor = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "HyPanel-Telemetrie"); t.setDaemon(true); return t;
        });
        executor.scheduleWithFixedDelay(this::sample, 5, 5, TimeUnit.SECONDS);
    }
    @Override protected void shutdown() {
        if (executor != null) executor.shutdownNow();
        try { Files.deleteIfExists(Path.of(".hypanel-telemetry.json")); } catch (Exception ignored) {}
        try { Files.deleteIfExists(Path.of(".hypanel-auth.json")); } catch (Exception ignored) {}
    }
    private static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            if (c == '"' || c == '\\') out.append('\\').append(c);
            else if (c < 32) out.append(String.format("\\u%04x", (int)c));
            else out.append(c);
        }
        return out.append('"').toString();
    }
    private void sample() {
        try {
            try { sampleAuth(); } catch (Exception ignored) {
                // Veralteter Auth-Status verfällt, Spielmesswerte bleiben unabhängig.
            }
            if ("false".equalsIgnoreCase(System.getenv("HYPANEL_TELEMETRY"))) return;
            Universe universe = Universe.get();
            if (universe == null) return;
            long now = System.nanoTime();
            List<String> players = new ArrayList<>(), worlds = new ArrayList<>();
            for (var player : universe.getPlayers()) {
                players.add("{\"uuid\":" + quote(player.getUuid().toString()) + ",\"name\":" + quote(player.getUsername()) + "}");
            }
            Set<String> active = new HashSet<>();
            for (var entry : universe.getWorlds().entrySet()) {
                String name = entry.getKey(); World world = entry.getValue();
                active.add(name);
                long tick = world.getTick();
                long[] old = previous.put(name, new long[]{tick, now, System.identityHashCode(world)});
                Double tps = null;
                if (old != null && old[2] == System.identityHashCode(world) && tick >= old[0]) {
                    double seconds = (now - old[1]) / 1e9;
                    double measured = (tick - old[0]) / seconds;
                    if (seconds >= 4 && seconds <= 20 && measured >= 0 && measured <= 200) tps = measured;
                }
                worlds.add("{\"name\":" + quote(name) + ",\"tick\":" + tick + ",\"tps\":" + tps + ",\"paused\":" + world.isPaused() + "}");
            }
            previous.keySet().retainAll(active);
            String output = "{\"at\":" + (System.currentTimeMillis() / 1000.0) + ",\"source\":\"HyPanelTelemetry / World.getTick\",\"players\":[" + String.join(",", players) + "],\"worlds\":[" + String.join(",", worlds) + "]}";
            Path temp = Path.of(".hypanel-telemetry.tmp"), destination = Path.of(".hypanel-telemetry.json");
            Files.writeString(temp, output);
            Files.move(temp, destination, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (Exception error) {
            // A stale sample expires in the panel. Never fabricate measurements.
            System.err.println("[HyPanel] Telemetrie konnte nicht aktualisiert werden: " + error.getClass().getSimpleName());
        }
    }

    private void sampleAuth() throws Exception {
        var auth = ServerAuthManager.getInstance();
        var profile = auth.getSelectedProfile();
        var pending = auth.getPendingProfiles();
        List<String> profiles = new ArrayList<>();
        if (pending != null) for (int i = 0; i < pending.length; i++) {
            profiles.add("{\"index\":" + (i + 1) + ",\"name\":" + quote(pending[i].username == null ? "Unbekannt" : pending[i].username) + "}");
        }
        var provider = HytaleServer.get().getConfig().getAuthCredentialStoreProvider();
        boolean encrypted = provider != null && provider.getClass().getSimpleName().toLowerCase(Locale.ROOT).contains("encrypted");
        var expiry = auth.getTokenExpiry();
        boolean valid = auth.hasSessionToken() && auth.hasIdentityToken() && (expiry == null || expiry.isAfter(java.time.Instant.now()));
        String output = "{\"at\":" + System.currentTimeMillis() / 1000.0
            + ",\"authenticated\":" + valid + ",\"mode\":" + quote(auth.getAuthMode().name())
            + ",\"profile\":" + (profile == null ? "null" : quote(profile.username == null ? "Unbekannt" : profile.username))
            + ",\"encrypted\":" + encrypted + ",\"profiles\":[" + String.join(",", profiles) + "]}";
        // Only status and profile names leave the game process, never OAuth/session tokens.
        Path temporary = Path.of(".hypanel-auth.tmp");
        Files.writeString(temporary, output);
        Files.move(temporary, Path.of(".hypanel-auth.json"), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
}
