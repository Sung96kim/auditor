type Service = { id: string; name: string; bytes: number; healthy: boolean };

export function ServiceRow({ service, onSelect }: { service: Service; onSelect: (id: string) => void }) {
  return (
    <tr onClick={() => onSelect(service.id)}>
      <td>
        <span className="status">{service.healthy ? "up" : "down"}</span>
      </td>
      <td>{service.name}</td>
      <td>{humanSize(service.bytes)}</td>
    </tr>
  );
}

function humanSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
