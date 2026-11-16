// Internal restore helper. Read credentials from stdin; never log the request.
const fs = require("node:fs");
const { io } = require("socket.io-client");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const socket = io("http://127.0.0.1:3001", { transports: ["websocket"] });
const deadline = setTimeout(() => finish(false, "Kuma did not respond"), 30000);
function finish(ok, message, extra = {}) {
    clearTimeout(deadline);
    console.log(JSON.stringify({ ok, message, ...extra }));
    socket.close();
    process.exit(ok ? 0 : 1);
}
socket.on("connect_error", () => finish(false, "Kuma connection failed"));
socket.on("connect", () => {
    // Kuma sends server info before registering its asynchronous socket handlers.
    setTimeout(() => {
        if (input.mode === "setup") {
            socket.emit("setup", input.username, input.password,
                result => finish(result.ok, result.ok ? "Administrator created" : "Administrator setup failed"));
            return;
        }
        socket.emit("login", { username: input.username, password: input.password, token: "" }, result => {
            if (!result.ok) return finish(false, "Administrator login failed");
            socket.once("monitorList", monitors => {
                const count = Object.keys(monitors).length;
                if (count !== input.expectedMonitors) return finish(false, "Restored monitor count differs", { count });
                if (input.mode === "verify") return finish(true, "Login and monitor list verified", { count });
                const url = new URL(input.ntfyUrl);
                const notification = {
                    name: "ntfy", type: "ntfy", ntfyserverurl: url.origin,
                    ntfytopic: url.pathname.slice(1), ntfyPriority: 3,
                    ntfyAuthenticationMethod: "none", isDefault: true, applyExisting: true,
                };
                socket.emit("addNotification", notification, null, saved => {
                    finish(saved.ok, saved.ok ? "Notification configured" : "Notification setup failed", { count });
                });
            });
            socket.emit("getMonitorList", () => {});
        });
    }, 1000);
});
