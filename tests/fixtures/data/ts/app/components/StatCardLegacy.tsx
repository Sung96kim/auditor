interface LegacyProps {
  title: string;
  amount: string;
  trend: number;
  url: string;
}

// The old card kept around "just for the settings page" — structurally identical to StatCard,
// so the two have drifted into parallel implementations that should be one component.
export function StatCardLegacy({ title, amount, trend, url }: LegacyProps) {
  return (
    <article className="stat" data-tone="neutral" onClick={() => undefined}>
      <header>
        <span>{title}</span>
      </header>
      <strong>{amount}</strong>
      <small>{trend >= 0 ? `+${trend}` : trend}</small>
      <a href={url}>details</a>
    </article>
  );
}
