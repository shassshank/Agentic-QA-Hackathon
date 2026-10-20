import { useEffect, useState } from "react";
import { api } from "../api/client";

/** Small connection indicator backed by GET /health. Shows no customer data. */
export function ApiHealth() {
  const [state, setState] = useState<"checking" | "online" | "offline">("checking");

  useEffect(() => {
    const controller = new AbortController();
    api.health(controller.signal).then(
      (res) => !controller.signal.aborted && setState(res.status === "ok" ? "online" : "offline"),
      () => !controller.signal.aborted && setState("offline"),
    );
    return () => controller.abort();
  }, []);

  const label = { checking: "Checking service…", online: "Service online", offline: "Service unreachable" }[state];
  return (
    <span className={`health health--${state}`}>
      <span className="health__dot" aria-hidden="true" />
      {label}
    </span>
  );
}
