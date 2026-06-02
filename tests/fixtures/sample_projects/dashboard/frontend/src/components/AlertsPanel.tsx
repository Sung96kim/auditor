import { Button } from "@/components/ui";
import { useState } from "react";
import { useEffect } from "react";

type Alert = { id: string; severity: string };

export function AlertsPanel({ alerts }: { alerts: Alert[] }) {
  const [acked, setAcked] = useState<string[]>([]);
  useEffect(() => setAcked([]), []);
  return (
    <div className="alerts">
      <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-red-700">
        {alerts.length}
      </span>
      <Button className="h-8 w-8">Ack</Button>
    </div>
  );
}

function toKib(n: number) {
  const v = n / 1024;
  return v.toFixed(1);
}

function toMib(n: number) {
  const v = n / 1048576;
  return v.toFixed(1);
}
