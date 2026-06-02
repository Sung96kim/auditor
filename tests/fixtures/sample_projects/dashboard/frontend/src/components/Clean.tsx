import { Badge, Card } from "@/lib/ui";

export function ServiceSummary({ name, healthy }: { name: string; healthy: boolean }) {
  return (
    <Card>
      <h3>{name}</h3>
      <Badge tone={healthy ? "emerald" : "red"}>{healthy ? "Healthy" : "Down"}</Badge>
    </Card>
  );
}
