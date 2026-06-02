type Host = { id: string; name: string; bytes: number; healthy: boolean };

export function HostRow({ host, onSelect }: { host: Host; onSelect: (id: string) => void }) {
  return (
    <tr onClick={() => onSelect(host.id)}>
      <td>
        <span className="status">{host.healthy ? "up" : "down"}</span>
      </td>
      <td>{host.name}</td>
      <td>{humanSize(host.bytes)}</td>
    </tr>
  );
}

function humanSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
